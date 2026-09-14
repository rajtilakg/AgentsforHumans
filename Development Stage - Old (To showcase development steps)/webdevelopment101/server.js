require('dotenv').config();
const express = require('express');
const cookieParser = require('cookie-parser');
const axios = require('axios');
const cors = require('cors');
const path = require('path');
const multer = require('multer');
const { CognitoJwtVerifier } = require('aws-jwt-verify');
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');
const { S3Client, PutObjectCommand } = require('@aws-sdk/client-s3');

const app = express();
const PORT = process.env.PORT || 3000;
const REGION = process.env.AWS_REGION || 'ap-south-1';

// AWS SDK Clients (Uses IAM Role credentials automatically)
const ddbClient = new DynamoDBClient({ region: REGION });
const docClient = DynamoDBDocumentClient.from(ddbClient);
const s3Client = new S3Client({ region: REGION });

// Multer in-memory storage for handling image uploads
const upload = multer({ storage: multer.memoryStorage() });

// Middleware
app.use(cookieParser());
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(cors());

// Serve Static Files
app.use(express.static(__dirname));
app.use('/backend', express.static(path.join(__dirname, 'backend')));

// Cognito JWT Verifier
const jwtVerifier = CognitoJwtVerifier.create({
  userPoolId: process.env.COGNITO_USER_POOL_ID,
  tokenUse: "access",
  clientId: process.env.COGNITO_CLIENT_ID,
});

// Middleware to Protect Routes & Extract the Unique Cognito User ID
const requireAuth = async (req, res, next) => {
  const accessToken = req.cookies.accessToken;
  if (!accessToken) return res.status(401).send('Session expired or missing token. Please <a href="/login">log in</a>.');

  try {
    const payload = await jwtVerifier.verify(accessToken);
    req.user = payload; // req.user.sub contains the unique Cognito ID
    next();
  } catch (err) {
    return res.status(401).send('Invalid token. <a href="/login">Login again</a>');
  }
};

// --- ROUTES ---

// 1. Landing Page
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'home.html'));
});

// 2. Cognito Login Redirect
app.get('/login', (req, res) => {
  const cognitoLoginUrl = `https://${process.env.COGNITO_DOMAIN}/login?client_id=${process.env.COGNITO_CLIENT_ID}&response_type=code&scope=email+openid+profile&redirect_uri=${encodeURIComponent(process.env.REDIRECT_URI)}`;
  res.redirect(cognitoLoginUrl);
});

// 3. OAuth Callback Handler
app.get('/auth/callback', async (req, res) => {
  const authCode = req.query.code;
  if (!authCode) return res.status(400).send('Authorization code not found.');

  try {
    const tokenUrl = `https://${process.env.COGNITO_DOMAIN}/oauth2/token`;
    const params = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: process.env.COGNITO_CLIENT_ID,
      client_secret: process.env.COGNITO_CLIENT_SECRET, // <-- ADD THIS EXACT LINE
      code: authCode,
      redirect_uri: process.env.REDIRECT_URI,
    });

    const response = await axios.post(tokenUrl, params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });

    const { access_token, id_token } = response.data;
    
    // Store tokens securely in cookies
    res.cookie('accessToken', access_token, { httpOnly: true, secure: true, sameSite: 'lax' });
    res.cookie('idToken', id_token, { httpOnly: true, secure: true, sameSite: 'lax' });

    // Redirect to Q&A session after successful login
    res.redirect('/backend/Qa-session.html');
  } catch (error) {
    console.error('Token exchange error:', error.response?.data || error.message);
    res.status(500).send('Authentication failed.');
  }
});

// 4. Save Q&A Answers to DynamoDB (Protected)
app.post('/save-answers', requireAuth, async (req, res) => {
  const { patientAnswers } = req.body;
  const userId = req.user.sub; // Extract unique Cognito ID

  const command = new PutCommand({
    TableName: process.env.DYNAMODB_TABLE || 'DermaCareUsers',
    Item: {
      user_id: userId, // <-- EXACT MATCH FOR YOUR DYNAMODB PARTITION KEY
      patientAnswers: patientAnswers,
      updatedAt: new Date().toISOString(),
    },
  });

  try {
    await docClient.send(command);
    console.log(`Successfully created/updated record for user_id: ${userId}`);
    res.status(200).json({ status: 'success', message: 'Answers saved to DynamoDB.' });
  } catch (err) {
    console.error('DynamoDB Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to write to database.' });
  }
});

// 5. Upload Image to S3 (Protected)
app.post('/upload-image', requireAuth, upload.single('image'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ status: 'error', message: 'No file uploaded.' });
  }

  const userId = req.user.sub;
  
  // Filename format: user_id-timestamp-originalName (so Lambda knows who it belongs to)
  const fileName = `${userId}-${Date.now()}-${req.file.originalname || 'image.jpg'}`;

  const s3Command = new PutObjectCommand({
    Bucket: process.env.S3_BUCKET_NAME,
    Key: fileName,
    Body: req.file.buffer,
    ContentType: req.file.mimetype,
  });

  try {
    await s3Client.send(s3Command);
    console.log(`Image saved to S3 bucket: ${fileName}`);
    res.status(200).json({ status: 'success', imageUrl: `s3://${process.env.S3_BUCKET_NAME}/${fileName}` });
  } catch (err) {
    console.error('S3 Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to upload to S3.' });
  }
});

// 6. Logout
app.get('/logout', (req, res) => {
  res.clearCookie('accessToken');
  res.clearCookie('idToken');
  
  // Redirect to Cognito Hosted UI logout, then route back to your domain
  const logoutUrl = `https://${process.env.COGNITO_DOMAIN}/logout?client_id=${process.env.COGNITO_CLIENT_ID}&logout_uri=${encodeURIComponent('https://edermacare.duckdns.org/')}`;
  res.redirect(logoutUrl);
});

// Start Server
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
