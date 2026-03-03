// Global state
let messageCount = 0;
let lastSource = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('Initializing chatbot...');
    
    // Get initial status
    await updateStatus();
    
    // Load KB questions
    await loadKBQuestions();
    
    // Focus input
    document.getElementById('messageInput').focus();
});

// Update status indicator
async function updateStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        
        const statusIndicator = document.getElementById('statusIndicator');
        const statusText = statusIndicator.querySelector('.status-text');
        const statusDot = statusIndicator.querySelector('.status-dot');
        
        if (data.status === 'ready') {
            statusText.textContent = `Ready (${data.hardcoded_questions} Q&A)`;
            statusDot.style.backgroundColor = '#4baf50';
        } else {
            statusText.textContent = 'Error';
            statusDot.style.backgroundColor = '#dc3545';
        }
        
        // Update KB count in sidebar
        document.getElementById('kbCount').textContent = data.hardcoded_questions;
        
    } catch (error) {
        console.error('Error updating status:', error);
        const statusText = document.getElementById('statusIndicator').querySelector('.status-text');
        statusText.textContent = 'Offline';
    }
}

// Load KB questions
async function loadKBQuestions() {
    try {
        const response = await fetch('/api/questions');
        const data = await response.json();
        document.getElementById('kbCount').textContent = data.questions.length;
    } catch (error) {
        console.error('Error loading KB questions:', error);
    }
}

// Send message
async function sendMessage(event) {
    event.preventDefault();
    
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) return;
    
    // Disable send button
    const sendButton = document.getElementById('sendButton');
    sendButton.disabled = true;
    
    // Add user message to chat
    addMessage(message, 'user');
    
    // Clear input
    input.value = '';
    input.focus();
    
    // Show typing indicator
    const typingElement = addMessage('', 'assistant');
    typingElement.classList.add('typing');
    typingElement.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
    
    try {
        // Send message to backend
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message })
        });
        
        const data = await response.json();
        
        // Remove typing indicator
        typingElement.remove();
        
        if (response.ok) {
            // Add assistant message
            const assistantMsg = addMessage(data.response, 'assistant');
            
            // Add source badge
            const sourceElement = document.createElement('div');
            sourceElement.className = `source-badge source-${data.source}`;
            sourceElement.textContent = `✓ ${data.source.toUpperCase()} (${(data.confidence * 100).toFixed(0)}%)`;
            assistantMsg.appendChild(sourceElement);
            
            // Update last source in sidebar
            updateSourceInfo(data.source, data.confidence, data.retrieved_docs);
            
            messageCount++;
            document.getElementById('messageCount').textContent = messageCount;
            
        } else {
            addMessage(`Error: ${data.error || 'Unknown error'}`, 'assistant');
        }
        
    } catch (error) {
        console.error('Error sending message:', error);
        typingElement.remove();
        addMessage('Sorry, there was an error connecting to the server.', 'assistant');
    } finally {
        sendButton.disabled = false;
        input.focus();
    }
}

// Add message to chat
function addMessage(text, sender) {
    const messagesArea = document.getElementById('messagesArea');
    
    // Remove welcome message if this is the first real message
    const welcomeMsg = messagesArea.querySelector('.welcome-message');
    if (welcomeMsg && messageCount === 0) {
        welcomeMsg.remove();
    }
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}-message`;
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = text;
    
    messageDiv.appendChild(contentDiv);
    messagesArea.appendChild(messageDiv);
    
    // Auto-scroll to bottom
    setTimeout(() => {
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }, 0);
    
    return messageDiv;
}

// Update source info in sidebar
function updateSourceInfo(source, confidence, retrievedDocs) {
    const sourceInfo = document.getElementById('sourceInfo');
    
    const sourceLabels = {
        'knowledge_base': 'Knowledge Base',
        'rag': 'Document Retrieval (RAG)',
        'llm': 'LLM'
    };
    
    const sourceDescriptions = {
        'knowledge_base': 'Instant hardcoded answer',
        'rag': `${retrievedDocs} documents used for context`,
        'llm': 'AI-generated response'
    };
    
    sourceInfo.className = `source-info ${source === 'knowledge_base' ? 'kb' : source === 'rag' ? 'rag' : 'llm'}`;
    
    sourceInfo.innerHTML = `
        <div class="source-label">${sourceLabels[source]}</div>
        <div class="source-detail">${sourceDescriptions[source]}</div>
        <div class="source-detail">Confidence: ${(confidence * 100).toFixed(0)}%</div>
    `;
}

// Load documents
async function loadDocuments() {
    showLoading(true, 'Loading documents...');
    
    try {
        const response = await fetch('/api/load-docs', {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (response.ok) {
            addMessage('✓ Documents loaded successfully! You can now ask questions about the advising materials.', 'assistant');
            messageCount++;
            document.getElementById('messageCount').textContent = messageCount;
        } else {
            addMessage(`Error: ${data.error || 'Failed to load documents'}`, 'assistant');
        }
    } catch (error) {
        console.error('Error loading documents:', error);
        addMessage('Error: Failed to load documents', 'assistant');
    } finally {
        showLoading(false);
    }
}

// Show questions modal
async function showQuestions() {
    try {
        const response = await fetch('/api/questions');
        const data = await response.json();
        
        const modalBody = document.getElementById('questionsModalBody');
        const questionsList = document.createElement('div');
        questionsList.className = 'questions-list';
        
        data.questions.forEach(question => {
            const item = document.createElement('div');
            item.className = 'question-item';
            item.textContent = question;
            item.onclick = () => {
                document.getElementById('messageInput').value = question;
                closeModal('questionsModal');
                document.getElementById('messageInput').focus();
            };
            questionsList.appendChild(item);
        });
        
        modalBody.innerHTML = '';
        modalBody.appendChild(questionsList);
        
        openModal('questionsModal');
        
    } catch (error) {
        console.error('Error loading questions:', error);
        alert('Error loading questions');
    }
}

// Clear history
async function clearHistory() {
    if (!confirm('Clear conversation history?')) return;
    
    try {
        const response = await fetch('/api/clear-history', {
            method: 'POST'
        });
        
        if (response.ok) {
            // Clear chat area
            const messagesArea = document.getElementById('messagesArea');
            messagesArea.innerHTML = `
                <div class="message assistant-message welcome-message">
                    <div class="message-content">
                        <p><strong>Conversation cleared!</strong></p>
                        <p>Feel free to ask more questions.</p>
                    </div>
                </div>
            `;
            
            messageCount = 0;
            document.getElementById('messageCount').textContent = messageCount;
            document.getElementById('sourceInfo').innerHTML = '<p class="placeholder">No response yet</p>';
        }
    } catch (error) {
        console.error('Error clearing history:', error);
        alert('Error clearing history');
    }
}

// Modal functions
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.remove('active');
}

// Close modal when clicking outside
document.addEventListener('click', (event) => {
    const modals = document.querySelectorAll('.modal.active');
    modals.forEach(modal => {
        if (event.target === modal) {
            modal.classList.remove('active');
        }
    });
});

// Show loading indicator
function showLoading(show, text = 'Processing...') {
    const loadingModal = document.getElementById('loadingModal');
    const loadingText = document.getElementById('loadingText');
    
    if (show) {
        loadingText.textContent = text;
        loadingModal.classList.add('active');
    } else {
        loadingModal.classList.remove('active');
    }
}

// Add CSS for typing indicator
const style = document.createElement('style');
style.textContent = `
    .typing-indicator {
        display: flex;
        gap: 4px;
        align-items: center;
        height: 20px;
    }
    
    .typing-indicator span {
        width: 6px;
        height: 6px;
        background-color: currentColor;
        border-radius: 50%;
        animation: typing 1.4s infinite;
    }
    
    .typing-indicator span:nth-child(2) {
        animation-delay: 0.2s;
    }
    
    .typing-indicator span:nth-child(3) {
        animation-delay: 0.4s;
    }
    
    @keyframes typing {
        0%, 60%, 100% {
            opacity: 0.5;
            transform: translateY(0);
        }
        30% {
            opacity: 1;
            transform: translateY(-10px);
        }
    }
`;
document.head.appendChild(style);
