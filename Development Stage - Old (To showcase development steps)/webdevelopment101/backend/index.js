const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = 3000;

// Middleware
app.use(cors());
app.use(express.json());

// Serve static files (HTML, CSS, JS) from the current folder
app.use(express.static(path.join(__dirname)));

// Route to serve the Q&A HTML page on the root URL
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'qa-session.html'));
});

// Endpoint to receive Q&A data from the frontend
app.post('/save-answers', (req, res) => {
    const newAnswers = req.body;

    let existingData = [];

    // Check if database.json exists and read it
    if (fs.existsSync('./database.json')) {
        try {
            const fileData = fs.readFileSync('./database.json', 'utf8');
            existingData = JSON.parse(fileData);
        } catch (err) {
            console.error("Error reading file:", err);
        }
    }

    // Append new user answers
    existingData.push(newAnswers);

    // Save back to database.json
    fs.writeFile('./database.json', JSON.stringify(existingData, null, 4), (err) => {
        if (err) {
            console.error("Failed to save data:", err);
            return res.status(500).json({ status: "error", message: "Failed to save data." });
        }
        console.log("Saved new entry to database.json!");
        res.status(201).json({ status: "success", message: "Answers successfully saved!" });
    });
});

// Start server
app.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}`);
});