require('dotenv').config();
const express = require('express');
const cookieParser = require('cookie-parser');
const axios = require('axios');
const cors = require('cors');
const path = require('path');
const multer = require('multer');
const rateLimit = require('express-rate-limit');
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

const upload = multer({ 
  storage: multer.memoryStorage(),
  limits: { fileSize: 5 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    if (file.mimetype.startsWith('image/')) {
      cb(null, true);
    } else {
      cb(new Error('Only image files are allowed!'), false);
    }
  }
});

app.use(cookieParser());
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(cors({
  origin: 'https://edermacare.duckdns.org',
  credentials: true
}));

// ============================================================================
// RATE LIMITING CONFIGURATION
// ============================================================================
// Designed to align with Gemini API free tier limits:
// - 15 requests/minute (RPM)
// - 250K tokens/minute (TPM)
// - 500 requests/day (RPD)
//
// User limits are set generously for demo/judging purposes while showing
// production-ready rate limiting implementation.
// ============================================================================

// General API rate limiter - applies to most endpoints
const generalLimiter = rateLimit({
  windowMs: 1 * 60 * 1000, // 1 minute window
  max: 50, // 50 requests per minute per user
  message: {
    status: 'error',
    message: 'Too many requests. Please wait a moment and try again.',
    retryAfter: '1 minute'
  },
  standardHeaders: true, // Return rate limit info in `RateLimit-*` headers
  legacyHeaders: false, // Disable `X-RateLimit-*` headers
  // Use user ID from JWT for per-user rate limiting
  keyGenerator: (req) => {
    return req.user?.sub || req.ip; // Fallback to IP if not authenticated
  },
  handler: (req, res) => {
    res.status(429).json({
      status: 'error',
      message: '⏱️ You\'re going too fast! Please wait a moment before trying again.',
      retryAfter: Math.ceil(req.rateLimit.resetTime / 1000),
      tip: 'Rate limits help us keep the service running smoothly for everyone.'
    });
  }
});

// Strict limiter for AI chat endpoint (most expensive operation)
const chatLimiter = rateLimit({
  windowMs: 1 * 60 * 1000, // 1 minute
  max: 10, // 10 chat requests per minute (aligns with 15 RPM Gemini limit)
  message: {
    status: 'error',
    message: 'Chat rate limit exceeded. Please wait before sending another message.',
  },
  keyGenerator: (req) => req.user?.sub || req.ip,
  handler: (req, res) => {
    res.status(429).json({
      status: 'error',
      message: '💬 You\'re chatting too quickly! Our AI needs a moment to breathe.',
      retryAfter: Math.ceil(req.rateLimit.resetTime / 1000),
      tip: 'Limited to 10 messages per minute to ensure quality responses for everyone.'
    });
  }
});

// Daily limiter for expensive operations
const dailyLimiter = rateLimit({
  windowMs: 24 * 60 * 60 * 1000, // 24 hours
  max: 400, // 400 requests per day (well under 500 RPD Gemini limit)
  skipFailedRequests: true, // Don't count failed requests
  keyGenerator: (req) => req.user?.sub || req.ip,
  handler: (req, res) => {
    res.status(429).json({
      status: 'error',
      message: '📅 Daily limit reached. You\'ve used your quota for today.',
      retryAfter: Math.ceil(req.rateLimit.resetTime / 1000),
      tip: 'Your limit will reset in 24 hours. Thanks for using DermaCare responsibly!'
    });
  }
});

// Image upload limiter (prevent storage abuse)
const uploadLimiter = rateLimit({
  windowMs: 1 * 60 * 1000, // 1 minute
  max: 20, // 20 uploads per minute (very generous for demo)
  keyGenerator: (req) => req.user?.sub || req.ip,
  handler: (req, res) => {
    res.status(429).json({
      status: 'error',
      message: '📸 Upload limit reached. Please wait before uploading more images.',
      retryAfter: Math.ceil(req.rateLimit.resetTime / 1000),
      tip: 'You can upload up to 20 images per minute.'
    });
  }
});

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

app.get('/qa', requireAuth, (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'Qa-session.html'));
});

app.get('/chat', requireAuth, (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'Main.html'));
});

app.get('/about', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'about.html'));
});

app.get('/treatments', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'treatments.html'));
});

app.use(express.static(path.join(__dirname, 'public')));
// Removed insecure backend static route


app.get('/privacy', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'privacy.html'));
});

app.get('/tos', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'tos.html'));
});

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'home.html'));
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

    res.redirect('/qa');
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


app.post('/save-answers', requireAuth, generalLimiter, async (req, res) => {
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

app.post('/upload-image', requireAuth, uploadLimiter, upload.single('image'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ status: 'error', message: 'No file uploaded.' });
  }

  const userId = req.user.sub;
  // Route to transient folder if requested, otherwise default to uploads/
  const folder = req.body.type === 'transient' ? 'transient' : 'uploads';
  const fileName = `${folder}/${userId}/${Date.now()}-${req.file.originalname || 'image.jpg'}`;

  const s3Command = new PutObjectCommand({
    Bucket: process.env.S3_BUCKET_NAME,
    Key: fileName,
    Body: req.file.buffer,
    ContentType: req.file.mimetype,
  });

  try {
    await s3Client.send(s3Command);
    res.status(200).json({ status: 'success', imageUrl: `s3://${process.env.S3_BUCKET_NAME}/${fileName}`, key: fileName });
  } catch (err) {
    console.error('S3 Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to upload to S3.' });
  }
});

app.post('/remove-image', requireAuth, generalLimiter, async (req, res) => {
  const { imageKey } = req.body;
  const userId = req.user.sub;

  if (!imageKey || !imageKey.includes(userId)) {
    return res.status(403).json({ status: 'error', message: 'Unauthorized action.' });
  }

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
    let contexts = response.Item?.product_contexts || {};
    
    images = images.filter(key => key !== imageKey);
    delete contexts[imageKey];

    const updateCmd = new UpdateCommand({
      TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
      Key: { user_id: userId },
      UpdateExpression: "SET scanned_images = :imgs, product_contexts = :ctx",
      ExpressionAttributeValues: { ":imgs": images, ":ctx": contexts }
    });
    await docClient.send(updateCmd);

    res.status(200).json({ status: 'success' });
  } catch (err) {
    console.error('Delete Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to remove image.' });
  }
});

app.get('/get-user-images', requireAuth, generalLimiter, async (req, res) => {
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
    
    res.status(200).json({ status: 'success', images: imageObjects, contexts: response.Item?.product_contexts || {} });
  } catch (err) {
    console.error('DynamoDB/S3 Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to fetch images.' });
  }
});


app.post('/save-context', requireAuth, generalLimiter, async (req, res) => {
  const { imageKey, text, time } = req.body;
  const userId = req.user.sub;

  if (!imageKey || !imageKey.includes(userId)) {
    return res.status(403).json({ status: 'error', message: 'Unauthorized action.' });
  }

  try {
    const getCmd = new GetCommand({
      TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
      Key: { user_id: userId }
    });
    const response = await docClient.send(getCmd);
    let contexts = response.Item?.product_contexts || {};
    
    contexts[imageKey] = { text, time };

    const updateCmd = new UpdateCommand({
      TableName: process.env.DYNAMODB_TABLE || 'UserSkinProfiles',
      Key: { user_id: userId },
      UpdateExpression: "SET product_contexts = :ctx",
      ExpressionAttributeValues: { ":ctx": contexts }
    });
    await docClient.send(updateCmd);
    
    res.status(200).json({ status: 'success' });
  } catch (err) {
    console.error('Context Save Error:', err);
    res.status(500).json({ status: 'error', message: 'Failed to save context.' });
  }
});

// UPDATED CHAT ROUTE FOR STREAMING
app.post('/chat', requireAuth, chatLimiter, dailyLimiter, async (req, res) => {
  const { message, history, inline_image_keys } = req.body;
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
      inline_image_keys || [],
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
