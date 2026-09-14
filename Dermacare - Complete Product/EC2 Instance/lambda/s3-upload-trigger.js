/**
 * AWS Lambda Function: S3 Upload Trigger
 * 
 * Triggered when a user uploads a product image to S3 (uploads/ folder).
 * Updates DynamoDB to track the user's scanned product images (max 5, FIFO).
 * 
 * Trigger: S3 Event (PUT Object)
 * Prefix: uploads/
 * Runtime: Node.js 18.x
 * Timeout: 10 seconds
 * Memory: 256 MB
 * 
 * Required IAM Permissions:
 * - s3:GetObject (read from bucket)
 * - dynamodb:GetItem (read user profile)
 * - dynamodb:UpdateItem (update user profile)
 */

const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, GetCommand, UpdateCommand } = require('@aws-sdk/lib-dynamodb');

// Initialize DynamoDB client
const ddbClient = new DynamoDBClient({ region: process.env.AWS_REGION || 'ap-south-1' });
const docClient = DynamoDBDocumentClient.from(ddbClient);

const TABLE_NAME = process.env.DYNAMODB_TABLE || 'UserSkinProfiles';

/**
 * Lambda handler function
 * @param {Object} event - S3 event object
 * @param {Object} context - Lambda context
 */
exports.handler = async (event, context) => {
    console.log('Lambda triggered:', JSON.stringify(event, null, 2));

    try {
        // Extract S3 bucket and object key from event
        const record = event.Records[0];
        const bucket = record.s3.bucket.name;
        const rawKey = record.s3.object.key;
        
        // Decode URL-encoded key (handles spaces and special characters)
        const key = decodeURIComponent(rawKey.replace(/\+/g, ' '));
        
        console.log(`Processing file: ${key} from bucket: ${bucket}`);
        
        // Validate path structure: uploads/{user_id}/{timestamp}-{filename}
        const pathParts = key.split('/');
        
        if (pathParts.length < 3 || pathParts[0] !== 'uploads') {
            console.warn(`Ignoring file: Path does not match expected structure (uploads/{user_id}/{filename})`);
            return {
                statusCode: 200,
                body: JSON.stringify({ message: 'Ignored - invalid path structure' })
            };
        }
        
        // Extract user_id from path
        const userId = pathParts[1];
        console.log(`Extracted user_id: ${userId}`);
        
        // Get current user profile from DynamoDB
        const getCommand = new GetCommand({
            TableName: TABLE_NAME,
            Key: { user_id: userId }
        });
        
        const response = await docClient.send(getCommand);
        const item = response.Item || {};
        
        // Get existing scanned_images array (or initialize empty)
        let scannedImages = item.scanned_images || [];
        console.log(`Current scanned_images count: ${scannedImages.length}`);
        
        // Add new image key to array
        scannedImages.push(key);
        console.log(`Added new image: ${key}`);
        
        // Enforce 5-image limit (FIFO - First In, First Out)
        if (scannedImages.length > 5) {
            const removed = scannedImages.slice(0, scannedImages.length - 5);
            scannedImages = scannedImages.slice(-5); // Keep last 5
            console.log(`Removed oldest images to maintain 5-image limit:`, removed);
        }
        
        // Update DynamoDB with new scanned_images array
        const updateCommand = new UpdateCommand({
            TableName: TABLE_NAME,
            Key: { user_id: userId },
            UpdateExpression: 'SET scanned_images = :img_list, updatedAt = :timestamp',
            ExpressionAttributeValues: {
                ':img_list': scannedImages,
                ':timestamp': new Date().toISOString()
            },
            ReturnValues: 'UPDATED_NEW'
        });
        
        const updateResponse = await docClient.send(updateCommand);
        console.log('DynamoDB updated successfully:', updateResponse.Attributes);
        
        return {
            statusCode: 200,
            body: JSON.stringify({
                message: 'Database updated successfully with 5-image limit',
                userId: userId,
                imageCount: scannedImages.length,
                images: scannedImages
            })
        };
        
    } catch (error) {
        console.error('Error processing S3 event:', error);
        
        // Log error details for debugging
        console.error('Error details:', {
            message: error.message,
            stack: error.stack,
            code: error.code
        });
        
        // Don't throw error - Lambda will retry and potentially cause duplicate processing
        // Return success to prevent retries
        return {
            statusCode: 500,
            body: JSON.stringify({
                error: 'Failed to process upload',
                details: error.message
            })
        };
    }
};
