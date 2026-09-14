// backend/gemini.js
//
// This connects the Node.js server to your internal Python Strands agent.

async function callGeminiAgent(message, history, userId, inlineImageKeys, res) {
  try {
    // Make a POST request to the internal Python FastAPI server
    const response = await fetch('http://127.0.0.1:8000/invoke', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        message: message,
        history: history,
        user_id: userId,
        inline_image_keys: inlineImageKeys 
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    // Read the streaming response from Python FastAPI
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // Pipe chunks directly to the UI
        res.write(decoder.decode(value));
    }
    
    // Close the connection
    res.end();

  } catch (error) {
    console.error("Error communicating with Python backend:", error);
    res.write("The skincare agent is currently analyzing a heavy load or is offline. Please try again in a moment.");
    res.end();
  }
}

module.exports = { callGeminiAgent };
