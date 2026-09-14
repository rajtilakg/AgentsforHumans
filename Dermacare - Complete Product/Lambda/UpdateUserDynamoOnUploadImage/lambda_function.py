import json
import boto3
import urllib.parse

# Initialize the DynamoDB resource outside the handler for performance
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('UserSkinProfiles')

def lambda_handler(event, context):
    try:
        # 1. Extract bucket name and object key from the S3 event
        bucket = event['Records'][0]['s3']['bucket']['name']
        
        # Keys must be unquoted in case there are spaces in the filename
        raw_key = event['Records'][0]['s3']['object']['key']
        key = urllib.parse.unquote_plus(raw_key, encoding='utf-8')
        
        print(f"Triggered by file upload: {key} in bucket: {bucket}")
        
        # 2. Extract the user_id from the file path
        # Matches format: uploads/user_001/1715623000-image.jpg
        parts = key.split('/')
        if len(parts) < 3 or parts[0] != 'uploads':
            print("Ignoring file: Path does not match expected structure.")
            return {'statusCode': 200, 'body': 'Ignored'}
            
        user_id = parts[1]
        
        # 3. Retrieve the current user record to get the existing images list
        response = table.get_item(Key={'user_id': user_id})
        item = response.get('Item', {})
        
        # Get existing list or initialize a new one if it doesn't exist
        # Renamed field to 'scanned_images' to indicate an array
        scanned_images = item.get('scanned_images', [])
        
        # 4. Append the new image key to the list
        scanned_images.append(key)
        
        # 5. Enforce the limit of 5 images (FIFO)
        if len(scanned_images) > 5:
            # Slices the array to keep only the last 5 elements, discarding the oldest
            scanned_images = scanned_images[-5:]
            
        # 6. Update the DynamoDB table with the limited list
        table.update_item(
            Key={'user_id': user_id},
            UpdateExpression="SET scanned_images = :img_list",
            ExpressionAttributeValues={
                ":img_list": scanned_images
            },
            ReturnValues="UPDATED_NEW"
        )
        
        print(f"Successfully updated DynamoDB for user {user_id}. Kept {len(scanned_images)} images.")
        
        return {
            'statusCode': 200,
            'body': json.dumps('Database updated successfully with 5-image limit!')
        }
        
    except Exception as e:
        print(f"Error processing S3 event: {str(e)}")
        raise e