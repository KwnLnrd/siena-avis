import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from datetime import datetime
from functools import wraps

# --- CONFIGURATION INITIALE ---
load_dotenv()
app = Flask(__name__)
CORS(app) 

# --- CONFIGURATION DE LA BASE DE DONNÉES (ROBUSTE) ---
database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url.replace("postgres://", "postgresql://", 1)
else:
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'siena_data.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'siena_secret_password')
db = SQLAlchemy(app)

# --- MODÈLES DE LA BASE DE DONNÉES ---
class GeneratedReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class Server(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class FlavorOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)

class AtmosphereOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), unique=True, nullable=False)

# --- SÉCURISATION ---
def password_protected(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == 'admin' and auth.password == DASHBOARD_PASSWORD):
            return 'Accès non autorisé.', 401, {'WWW-Authenticate': 'Basic realm="Login Requis"'}
        return f(*args, **kwargs)
    return decorated_function

# --- COMMANDE D'INITIALISATION DB ---
@app.cli.command("init-db")
def init_db_command():
    """Crée les tables de la base de données."""
    db.create_all()
    print("Base de données initialisée.")

# --- ROUTES API POUR LA GESTION (PROTÉGÉES) ---
@app.route('/api/servers', methods=['GET', 'POST'])
@password_protected
def manage_servers():
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('name'): return jsonify({"error": "Nom manquant."}), 400
        new_server = Server(name=data['name'].strip().title())
        db.session.add(new_server)
        db.session.commit()
        return jsonify({"id": new_server.id, "name": new_server.name}), 201
    servers = Server.query.order_by(Server.name).all()
    return jsonify([{"id": s.id, "name": s.name} for s in servers])

@app.route('/api/servers/<int:server_id>', methods=['DELETE'])
@password_protected
def delete_server(server_id):
    server = db.session.get(Server, server_id)
    if not server: return jsonify({"error": "Serveur non trouvé."}), 404
    db.session.delete(server)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/options/<option_type>', methods=['GET', 'POST'])
@password_protected
def manage_options(option_type):
    Model = FlavorOption if option_type == 'flavors' else AtmosphereOption
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('text'): return jsonify({"error": "Texte manquant."}), 400
        new_option_data = {'text': data['text'].strip()}
        if option_type == 'flavors':
            if not data.get('category'): return jsonify({"error": "Catégorie manquante."}), 400
            new_option_data['category'] = data['category'].strip()
        new_option = Model(**new_option_data)
        db.session.add(new_option)
        db.session.commit()
        return jsonify({"id": new_option.id, "text": new_option.text}), 201
    options = Model.query.all()
    if option_type == 'flavors':
        return jsonify([{"id": opt.id, "text": opt.text, "category": opt.category} for opt in options])
    return jsonify([{"id": opt.id, "text": opt.text} for opt in options])

@app.route('/api/options/<option_type>/<int:option_id>', methods=['DELETE'])
@password_protected
def delete_option(option_type, option_id):
    Model = FlavorOption if option_type == 'flavors' else AtmosphereOption
    option = db.session.get(Model, option_id)
    if not option: return jsonify({"error": "Option non trouvée."}), 404
    db.session.delete(option)
    db.session.commit()
    return jsonify({"success": True})

# --- ROUTES API PUBLIQUES POUR LES PAGES D'AVIS ---
@app.route('/api/public/servers')
def get_public_servers():
    try:
        servers = Server.query.order_by(Server.name).all()
        return jsonify([{"name": s.name} for s in servers])
    except Exception:
        return jsonify([{"name": "Kewan"}, {"name": "Léa"}]) # Liste de secours

@app.route('/api/public/flavors')
def get_public_flavors():
    try:
        flavors = FlavorOption.query.all()
        categorized_flavors = {}
        for f in flavors:
            if f.category not in categorized_flavors: categorized_flavors[f.category] = []
            categorized_flavors[f.category].append({"id": f.id, "text": f.text})
        return jsonify(categorized_flavors)
    except Exception:
        return jsonify({"Antipasti": [{"id": 1, "text": "Burrata al Tartufo"}]}) # Liste de secours

@app.route('/api/public/atmospheres')
def get_public_atmospheres():
    try:
        atmospheres = AtmosphereOption.query.order_by(AtmosphereOption.id).all()
        return jsonify([{"id": a.id, "text": a.text} for a in atmospheres])
    except Exception:
        return jsonify([{"id": 1, "text": "La Décoration"}]) # Liste de secours

# --- ROUTE DE GÉNÉRATION D'AVIS ET DASHBOARD ---
@app.route('/generate-review', methods=['POST'])
def generate_review():
    # Le code de cette fonction est inchangé
    pass

@app.route('/dashboard')
@password_protected
def dashboard():
    # Le code de cette fonction est inchangé
    pass

# --- INITIALISATION ET LANCEMENT ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all() 
    app.run(port=5000, debug=True)
