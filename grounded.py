import os
import sqlite3
from flask import Flask, request, jsonify
import ollama

app = Flask(__name__)
DB_FILE = "grounded.db"

def init_db():
    """Automatically creates the SQLite database and required tables if they do not exist."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Table to store uploaded source document notes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                content TEXT NOT NULL
            )
        ''')
        # Table to store the generated summary briefings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Table to store individual briefing line items, tracking metrics, and user decisions
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS briefing_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                briefing_id INTEGER,
                bullet_text TEXT NOT NULL,
                status TEXT CHECK(status IN ('cited', 'invented')),
                citation_source TEXT,
                user_decision TEXT DEFAULT 'pending' CHECK(user_decision IN ('pending', 'accepted', 'rejected', 'edited')),
                edited_text TEXT,
                FOREIGN KEY(briefing_id) REFERENCES briefings(id)
            )
        ''')
        conn.commit()

@app.route('/api/upload', methods=['POST'])
def upload_notes():
    """Handles multiple text file uploads from the Vue user interface."""
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided in request'}), 400
        
    uploaded_files = request.files.getlist('files')
    saved_count = 0
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        for file in uploaded_files:
            if file.filename.endswith('.txt'):
                file_content = file.read().decode('utf-8')
                cursor.execute(
                    "INSERT INTO source_notes (filename, content) VALUES (?, ?)",
                    (file.filename, file_content)
                )
                saved_count += 1
        conn.commit()
        
    return jsonify({'message': f'Successfully ingested {saved_count} source documents.'}), 200
# Continuation of app.py - Append this directly below the previous code block

@app.route('/api/generate', methods=['POST'])
def generate_briefing():
    """Pulls all stored source documents, queries Ollama, and strictly categorizes output."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        notes = cursor.execute("SELECT filename, content FROM source_notes").fetchall()
        
    if not notes:
        return jsonify({'error': 'No source notes found in database. Upload files first.'}), 400
        
    # Aggregate source documents into a single analytical context string
    aggregated_context = ""
    for note in notes:
        aggregated_context += f"--- START SOURCE FILE: {note['filename']} ---\n{note['content']}\n--- END SOURCE FILE ---\n\n"
        
    # Prompt engineering designed to strictly enforce factual accountability and expose fabrications
    system_prompt = (
        "You are an ironclad systems analyst validation system. Your task is to analyze the source notes provided "
        "and generate a cohesive technical briefing of exactly 5 to 8 bullet points.\n\n"
        "CRITICAL RULES:\n"
        "1. Every single bullet point must be explicitly accounted for.\n"
        "2. If a bullet point is supported by facts within the source files, prefix it exactly with: [CITED: filename.txt]\n"
        "3. If a bullet point contains assumptions, synthesizes data not explicitly written, or is invented by your model, "
        "you MUST prefix it exactly with: [INVENTED]\n"
        "4. Do not hide fabrications. If you make it up, mark it as [INVENTED].\n\n"
        "Format each line exactly like this example:\n"
        "[CITED: notes1.txt] The project framework requires an active SQLite database layer.\n"
        "[INVENTED] This infrastructure upgrade will reduce regional commuter delays by twenty percent."
    )
    
    try:
        response = ollama.chat(model='llama3', messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f"Here are the messy source notes to analyze:\n\n{aggregated_context}"}
        ])
        
        raw_output = response['message']['content']
        lines = raw_output.strip().split('\n')
        
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO briefings DEFAULT VALUES")
            briefing_id = cursor.lastrowid
            
            processed_items = []
            for line in lines:
                if not line.strip():
                    continue
                
                # Default safety values if parsing fails
                status = 'invented'
                citation_source = None
                clean_text = line
                
                # Algorithmic string classification logic independent of LLM wrapper
                if line.startswith('[CITED:'):
                    status = 'cited'
                    end_bracket_idx = line.find(']')
                    citation_source = line[7:end_bracket_idx].strip()
                    clean_text = line[end_bracket_idx+1:].strip()
                elif line.startswith('[INVENTED]'):
                    status = 'invented'
                    clean_text = line[10:].strip()
                
                cursor.execute(
                    "INSERT INTO briefing_items (briefing_id, bullet_text, status, citation_source) VALUES (?, ?, ?, ?)",
                    (briefing_id, clean_text, status, citation_source)
                )
                processed_items.append({
                    'briefing_id': briefing_id,
                    'bullet_text': clean_text,
                    'status': status,
                    'citation_source': citation_source,
                    'user_decision': 'pending'
                })
            conn.commit()
            
        return jsonify({'briefing_id': briefing_id, 'items': processed_items}), 200
        
    except Exception as e:
        return jsonify({'error': f'Ollama generation failed: {str(e)}'}), 500
# Continuation of app.py - Append this directly at the bottom to finalize the backend file

@app.route('/api/decision/<int:item_id>', methods=['PUT'])
def record_decision(item_id):
    """Persists human acceptance, rejection, or structural modification edits to a specific bullet point."""
    data = request.json
    decision = data.get('decision') # pending, accepted, rejected, edited
    edited_text = data.get('edited_text', None)
    
    if decision not in ['pending', 'accepted', 'rejected', 'edited']:
        return jsonify({'error': 'Invalid decision state code supplied.'}), 400
        
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE briefing_items SET user_decision = ?, edited_text = ? WHERE id = ?",
            (decision, edited_text, item_id)
        )
        conn.commit()
        
    return jsonify({'message': 'User verification data persisted successfully.'}), 200

@app.route('/api/briefings', methods=['GET'])
def get_historical_briefings():
    """Retrieves all past historical logs and associated decision metadata for system audit reviews."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        briefings = cursor.execute("SELECT * FROM briefings ORDER BY created_at DESC").fetchall()
        history = []
        
        for b in briefings:
            items = cursor.execute(
                "SELECT * FROM briefing_items WHERE briefing_id = ?", (b['id'],)
            ).fetchall()
            
            history.append({
                'id': b['id'],
                'created_at': b['created_at'],
                'items': [dict(item) for item in items]
            })
            
        return jsonify(history), 200

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)


