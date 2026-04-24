"""Flask web app for the iSchool Advising Chatbot."""
import sys
import os

# Add src to path so imports work consistently on Azure App Service.
# Depending on run-from-package / Oryx, __file__ may resolve under /tmp, but
# the built app lives under /home/site/wwwroot.
_src_candidates = [
    os.path.join(os.path.dirname(__file__), "src"),
    "/home/site/wwwroot/src",
]
for _p in _src_candidates:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from rag_chatbot import RAGAdvisingChatbot
from document_processor import setup_sample_docs
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _first_existing_dir(candidates: list[str], fallback: str) -> str:
    for d in candidates:
        try:
            if os.path.isdir(d):
                return d
        except Exception:
            pass
    return fallback

_TEMPLATES_DIR = _first_existing_dir(
    [os.path.join(_BASE_DIR, "templates"), "/home/site/wwwroot/templates"],
    os.path.join(_BASE_DIR, "templates"),
)
_STATIC_DIR = _first_existing_dir(
    [os.path.join(_BASE_DIR, "static"), "/home/site/wwwroot/static"],
    os.path.join(_BASE_DIR, "static"),
)

app = Flask(__name__, template_folder=_TEMPLATES_DIR, static_folder=_STATIC_DIR)


def _get_allowed_origins():
    """Read allowed CORS origins from environment."""
    raw = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5000,http://127.0.0.1:5000,https://ischooladvisingchatbot-dx4ynl.azurewebsites.net"
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


allowed_origins = _get_allowed_origins()
CORS(app, resources={r"/api/*": {"origins": allowed_origins}})
logger.info("CORS allowed origins: %s", allowed_origins)

# Initialize chatbot (singleton)
chatbot = None

def get_chatbot():
    """Get or create chatbot instance."""
    global chatbot
    if chatbot is None:
        try:
            chatbot = RAGAdvisingChatbot()
            logger.info("Chatbot initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize chatbot: {e}")
            raise
    return chatbot

# Routes

@app.route('/')
def index():
    """Serve main page."""
    try:
        # Prefer Jinja rendering so `url_for('static', ...)` resolves correctly.
        return render_template("index.html")
    except Exception as e:
        logger.warning("Failed to render templates/index.html, falling back to docs HTML: %s", e)

    # Absolute-URL fallback (works even if templates aren't discoverable).
    candidates = [
        os.path.join(_BASE_DIR, "docs", "index.html"),
        "/home/site/wwwroot/docs/index.html",
    ]
    for p in candidates:
        try:
            if os.path.isfile(p):
                return send_file(p)
        except Exception:
            pass

    return (
        "Chatbot backend is running, but the web UI HTML file was not found on the server. "
        "Try the API at /api/status or redeploy including the templates/docs folders."
    ), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat requests."""
    try:
        data = request.json
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return jsonify({'error': 'Empty message'}), 400
        
        # Get response from chatbot
        chatbot_instance = get_chatbot()
        result = chatbot_instance.chat(user_message)
        
        return jsonify({
            'response': result['response'],
            'source': result['source'],
            'confidence': result['confidence'],
            'retrieved_docs': len(result['retrieved_docs'])
        })
    
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def status():
    """Get chatbot status."""
    try:
        chatbot_instance = get_chatbot()
        providers = RAGAdvisingChatbot.get_available_providers()
        questions = chatbot_instance.get_knowledge_base_questions()
        
        return jsonify({
            'status': 'ready',
            'available_providers': providers,
            'hardcoded_questions': len(questions),
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/docs-stats', methods=['GET'])
def docs_stats():
    """Debug endpoint to verify which docs are being indexed on the server."""
    try:
        bot = get_chatbot()
        dp = bot.doc_processor
        scan = dp.scan_source_files()
        return jsonify({
            "docs_path": dp.docs_path,
            "scan": scan,
            "vector_store_chunks": dp.vector_store_count(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/history', methods=['GET'])
def history():
    """Get conversation history."""
    try:
        chatbot_instance = get_chatbot()
        hist = chatbot_instance.get_history()
        
        # Return only user messages and responses (not full metadata)
        simplified = [
            {
                'user': h['user_message'],
                'assistant': h['response'],
                'source': h['source']
            }
            for h in hist
        ]
        
        return jsonify({'history': simplified})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clear-history', methods=['POST'])
def clear_history():
    """Clear conversation history."""
    try:
        chatbot_instance = get_chatbot()
        chatbot_instance.clear_history()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/load-docs', methods=['POST'])
def load_docs():
    """Load and process documents."""
    try:
        chatbot_instance = get_chatbot()
        
        # Setup sample documents if they don't exist
        setup_sample_docs()
        
        # Process documents
        chatbot_instance.process_documents()
        
        return jsonify({'success': True, 'message': 'Documents loaded successfully'})
    except Exception as e:
        logger.error(f"Error loading documents: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/questions', methods=['GET'])
def get_questions():
    """Get all hardcoded questions."""
    try:
        chatbot_instance = get_chatbot()
        questions = chatbot_instance.get_knowledge_base_questions()
        return jsonify({'questions': questions})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
