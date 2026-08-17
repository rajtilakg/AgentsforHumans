require('dotenv').config();
const express = require('express');
const cookieParser = require('cookie-parser');
const axios = require('axios');
const { CognitoJwtVerifier } = require('aws-jwt-verify');

const app = express();
app.use(cookieParser());
app.use(express.urlencoded({ extended: true }));

const PORT = process.env.PORT || 3000;

// Initialize the Cognito JWT Verifier for access tokens
const jwtVerifier = CognitoJwtVerifier.create({
  userPoolId: process.env.COGNITO_USER_POOL_ID,
  tokenUse: "access",
  clientId: process.env.COGNITO_CLIENT_ID,
});

// 1. Home Page (Public)
app.get('/', (req, res) => {
  res.send(`
    <h2>Welcome to the App</h2>
    <a href="/login">Login with AWS Cognito</a> | 
    <a href="/dashboard">Go to Dashboard (Protected)</a>
  `);
});

// 2. Redirect to Cognito Hosted UI Login
app.get('/login', (req, res) => {
  const cognitoLoginUrl = `https://${process.env.COGNITO_DOMAIN}/login?client_id=${process.env.COGNITO_CLIENT_ID}&response_type=code&scope=email+openid+profile&redirect_uri=${encodeURIComponent(process.env.REDIRECT_URI)}`;
  res.redirect(cognitoLoginUrl);
});

// 3. Handle OAuth Callback & Token Exchange
app.get('/auth/callback', async (req, res) => {
  const authCode = req.query.code;
  if (!authCode) {
    return res.status(400).send('Authorization code not found.');
  }

  try {
    // Exchange authorization code for tokens
    const tokenUrl = `https://${process.env.COGNITO_DOMAIN}/oauth2/token`;
    const params = new URLSearchParams();
    params.append('grant_type', 'authorization_code');
    params.append('client_id', process.env.COGNITO_CLIENT_ID);
    params.append('code', authCode);
    params.append('redirect_uri', process.env.REDIRECT_URI);

    // If your app client has a secret, include it here:
    // params.append('client_secret', process.env.COGNITO_CLIENT_SECRET);

    const response = await axios.post(tokenUrl, params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });

    const { access_token, id_token, refresh_token } = response.data;

    // Store tokens securely in HttpOnly cookies
    res.cookie('accessToken', access_token, { httpOnly: true, secure: false }); // set secure: true in production (HTTPS)
    res.cookie('idToken', id_token, { httpOnly: true, secure: false });

    res.redirect('/dashboard');
  } catch (error) {
    console.error('Token exchange error:', error.response?.data || error.message);
    res.status(500).send('Authentication failed.');
  }
});

// Middleware to Protect Routes
const requireAuth = async (req, res, next) => {
  const accessToken = req.cookies.accessToken;

  if (!accessToken) {
    return res.redirect('/login');
  }

  try {
    // Verify the JWT signature and claims against AWS Cognito
    const payload = await jwtVerifier.verify(accessToken);
    req.user = payload;
    next();
  } catch (err) {
    console.error('Token verification failed:', err);
    return res.status(401).send('Session expired or invalid token. <a href="/login">Login again</a>');
  }
};

// 4. Protected Dashboard Route
app.get('/dashboard', requireAuth, (req, res) => {
  res.send(`
    <h2>Dashboard</h2>
    <p>Welcome! You are successfully authenticated.</p>
    <pre>${JSON.stringify(req.user, null, 2)}</pre>
    <br>
    <a href="/logout">Logout</a>
  `);
});

// 5. Logout Route
app.get('/logout', (req, res) => {
  res.clearCookie('accessToken');
  res.clearCookie('idToken');
  
  // Optionally redirect to Cognito logout endpoint to clear session globally
  const logoutUrl = `https://${process.env.COGNITO_DOMAIN}/logout?client_id=${process.env.COGNITO_CLIENT_ID}&logout_uri=${encodeURIComponent('http://localhost:3000/')}`;
  res.redirect(logoutUrl);
});

app.listen(PORT, () => {
  console.log(`Server is running on http://localhost:3000`);
});