require('dotenv').config();
const express = require('express');
const cookieParser = require('cookie-parser');
const axios = require('axios');
const cors = require('cors');
const path = require('path');
const multer = require('multer');
const { CognitoJwtVerifier } = require('aws-jwt-verify');
const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand, GetCommand, UpdateCommand } = require('@aws-sdk/lib-dynamodb');
const { S3Client, PutObjectCommand, GetObjectCommand, DeleteObjectCommand } = require('@aws-sdk/client-s3');
const { getSignedUrl } = require('@aws-sdk/s3-request-presigner');
const { callGeminiAgent } = require('./backend/gemini');

const app = express();
const PORT = process.env.PORT || 3000;
const REGION = process.env.AWS_REGION || 'ap-south-1';

const ddbClient = new DynamoDBClient({ region: REGION });
const docClient = DynamoDBDocumentClient.from(ddbClient);
const s3Client = new S3Client({ region: REGION });

const upload = multer({ storage: multer.memoryStorage() });

app.use(cookieParser());
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(cors());

const jwtVerifier = CognitoJwtVerifier.create({
  userPoolId: process.env.COGNITO_USER_POOL_ID,
  tokenUse: "access",
  clientId: process.env.COGNITO_CLIENT_ID,
});

const requireAuth = async (req, res, next) => {
  const accessToken = req.cookies.accessToken;
  if (!accessToken) return res.status(401).send('Session expired or missing token. Please <a href="/login">log in</a>.');

  try {
    const payload = await jwtVerifier.verify(accessToken);
    req.user = payload;
    next();
  } catch (err) {
    return res.status(401).send('Invalid token. <a href="/login">Login again</a>');
  }
};

app.get('/backend/Qa-session.html', requireAuth, (req, res) => {
  res.sendFile(path.join(__dirname, 'backend', 'Qa-session.html'));
});

app.get('/Main.html', requireAuth, (req, res) => {
  res.sendFile(path.join(__dirname, 'Main.html'));
});

app.use(express.static(__dirname));
app.use('/backend', express.static(path.join(__dirname, 'backend')));

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'home.html'));
});

app.get('/login', (req, res) => {
  const cognitoLoginUrl = `https://${process.env.COGNITO_DOMAIN}/login?client_id=${process.env.COGNITO_CLIENT_ID}&response_type=code&scope=email+openid+profile&redirect_uri=${encodeURIComponent(process.env.REDIRECT_URI)}`;
  res.redirect(cognitoLoginUrl);
});

app.get('/auth/callback', async (req, res) => {
  const authCode = req.query.code;
  if (!authCode) return res.status(400).send('Authorization code not found.');

  try {
    const tokenUrl = `https://${process.env.COGNITO_DOMAIN}/oauth2/token`;
    const params = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: process.env.COGNITO_CLIENT_ID,
      client_secret: process.env.COGNITO_CLIENT_SECRET,
      code: authCode,
      redirect_uri: process.env.REDIRECT_URI,
    });

    const response = await axios.post(tokenUrl, params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });

    const { access_token, id_token } = response.data;
    
    res.cookie('accessToken', access_token, { httpOnly: true, secure: true, sameSite: 'lax' });
    res.cookie('idToken', id_token, { httpOnly: true, secure: true, sameSite: 'lax' });

    res.redirect('/backend/Qa-session.html');
  } catch (error) {
    console.error('Token exchange error:', error.response?.data || error.message);
    res.status(500).send('Authentication failed.');
  }
});

// NEW DB CHECK ROUTE
app.get('/api/check-db', requireAuth, async (req, res) => {
    try {
        const command = new GetCommand({
            TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
            Key: { user_id: req.user.sub }
        });
        const response = await docClient.send(command);
        res.json({ hasAnswers: !!response.Item?.patientAnswers });
    } catch (err) {
        console.error("DB Check Error:", err);
        res.json({ hasAnswers: false });
    }
});


app.post('/save-answers', requireAuth, async (req, res) => {
  const { patientAnswers } = req.body;
  const userId = req.user.sub;

  const command = new PutCommand({
    TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
    Item: {
      user_id: userId,
      patientAnswers: patientAnswers,
      updatedAt: new Date().toISOString(),
    },
  });

  try {
    await docClient.send(command);
    res.status(200).json({ status: 'success', message: 'Answers saved to DynamoDB.' });
  } catch (err) {
    console.error('DynamoDB Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to write to database.' });
  }
});

app.post('/upload-image', requireAuth, upload.single('image'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ status: 'error', message: 'No file uploaded.' });
  }

  const userId = req.user.sub;
  const fileName = `uploads/${userId}/${Date.now()}-${req.file.originalname || 'image.jpg'}`;

  const s3Command = new PutObjectCommand({
    Bucket: process.env.S3_BUCKET_NAME,
    Key: fileName,
    Body: req.file.buffer,
    ContentType: req.file.mimetype,
  });

  try {
    await s3Client.send(s3Command);
    res.status(200).json({ status: 'success', imageUrl: `s3://${process.env.S3_BUCKET_NAME}/${fileName}` });
  } catch (err) {
    console.error('S3 Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to upload to S3.' });
  }
});

app.post('/remove-image', requireAuth, async (req, res) => {
  const { imageKey } = req.body;
  const userId = req.user.sub;

  try {
    await s3Client.send(new DeleteObjectCommand({
      Bucket: process.env.S3_BUCKET_NAME,
      Key: imageKey
    }));

    const getCmd = new GetCommand({
      TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
      Key: { user_id: userId }
    });
    const response = await docClient.send(getCmd);
    let images = response.Item?.scanned_images || [];
    
    images = images.filter(key => key !== imageKey);

    const updateCmd = new UpdateCommand({
      TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
      Key: { user_id: userId },
      UpdateExpression: "SET scanned_images = :imgs",
      ExpressionAttributeValues: { ":imgs": images }
    });
    await docClient.send(updateCmd);

    res.status(200).json({ status: 'success' });
  } catch (err) {
    console.error('Delete Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to remove image.' });
  }
});

app.get('/get-user-images', requireAuth, async (req, res) => {
  const userId = req.user.sub;
  try {
    const command = new GetCommand({
      TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
      Key: { user_id: userId }
    });
    
    const response = await docClient.send(command);
    const images = response.Item?.scanned_images || [];
    
    const imageObjects = await Promise.all(images.map(async (key) => {
      const getObjCmd = new GetObjectCommand({
        Bucket: process.env.S3_BUCKET_NAME,
        Key: key,
      });
      const url = await getSignedUrl(s3Client, getObjCmd, { expiresIn: 3600 });
      return { key, url };
    }));
    
    res.status(200).json({ status: 'success', images: imageObjects });
  } catch (err) {
    console.error('DynamoDB/S3 Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to fetch images.' });
  }
});

// UPDATED CHAT ROUTE FOR STREAMING
app.post('/chat', requireAuth, async (req, res) => {
  const { message, history } = req.body;
  const realUserId = req.user.sub; 

  if (!message || typeof message !== 'string' || !message.trim()) {
    return res.status(400).json({ status: 'error', message: 'Message is required.' });
  }

  // Set headers to disable all network buffering
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Transfer-Encoding', 'chunked');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no'); // Crucial for AWS/Nginx

  try {
    await callGeminiAgent(
      message.trim(), 
      Array.isArray(history) ? history : [], 
      realUserId,
      res // Pass Express response object to gemini.js
    );
    // Note: res.end() is handled inside callGeminiAgent
  } catch (err) {
    console.error('Chat endpoint error:', err);
    if (!res.headersSent) {
        res.status(500).json({ status: 'error', message: 'Server error' });
    } else {
        res.write("Server connection lost.");
        res.end();
    }
  }
});

// Auth Status Check for Dynamic Navbar
app.get('/api/auth-status', async (req, res) => {
  const accessToken = req.cookies.accessToken;
  if (!accessToken) return res.json({ isAuthenticated: false });

  try {
    await jwtVerifier.verify(accessToken);
    return res.json({ isAuthenticated: true });
  } catch (err) {
    return res.json({ isAuthenticated: false });
  }
});

app.get('/logout', (req, res) => {
  res.clearCookie('accessToken');
  res.clearCookie('idToken');
  const logoutUrl = `https://${process.env.COGNITO_DOMAIN}/logout?client_id=${process.env.COGNITO_CLIENT_ID}&logout_uri=${encodeURIComponent('https://edermacare.duckdns.org/')}`;
  res.redirect(logoutUrl);
});

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
