// backend/gemini.js
//
// This connects the Node.js server to your internal Python Strands agent.

async function callGeminiAgent(message, history = [], userId = "user_hackathon_final_test") {
  try {
    // Make a POST request to the internal Python FastAPI server
    // Node.js 18+ has native fetch() available globally.
    const response = await fetch('http://127.0.0.1:8000/invoke', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        message: message,
        history: history,
        user_id: userId 
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    
    // Return the markdown report string back to chat.html
    return data.reply; 

  } catch (error) {
    console.error("Error communicating with Python backend:", error);
    return "The skincare agent is currently analyzing a heavy load or is offline. Please try again in a moment.";
  }
}

module.exports = { callGeminiAgent };