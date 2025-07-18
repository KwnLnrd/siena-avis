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
# Charge les variables d'environnement depuis le fichier .env
load_dotenv()

# Initialise l'application Flask
app = Flask(__name__)
# Active CORS pour autoriser les requêtes depuis d'autres origines (utile pour le développement)
CORS(app) 

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
# Utilise la variable d'environnement DATABASE_URL (pour Heroku/Render) ou une base de données SQLite locale par défaut
database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    # Render utilise "postgres://", SQLAlchemy préfère "postgresql://"
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url.replace("postgres://", "postgresql://", 1)
else:
    # Si aucune DATABASE_URL n'est définie, on utilise un fichier de base de données local
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'siena_data.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Récupère le mot de passe du dashboard depuis les variables d'environnement ou utilise une valeur par défaut
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'siena_secret_password')
db = SQLAlchemy(app)

# --- MODÈLES DE LA BASE DE DONNÉES (TABLES) ---
class GeneratedReview(db.Model):
    """Table pour stocker chaque avis généré et le serveur associé."""
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class Server(db.Model):
    """Table pour stocker les noms des serveurs."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class FlavorOption(db.Model):
    """Table pour stocker les options de plats/saveurs."""
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)

class AtmosphereOption(db.Model):
    """Table pour stocker les options d'ambiance."""
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), unique=True, nullable=False)

# --- DÉCORATEUR DE SÉCURITÉ ---
def password_protected(f):
    """Un décorateur pour protéger une route avec une authentification basique."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        # Vérifie si l'utilisateur est 'admin' et si le mot de passe est correct
        if not auth or not (auth.username == 'admin' and auth.password == DASHBOARD_PASSWORD):
            return 'Accès non autorisé.', 401, {'WWW-Authenticate': 'Basic realm="Login Requis"'}
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES API (PROTÉGÉES) POUR LA GESTION ---
# Ces routes nécessitent le mot de passe pour fonctionner

@app.route('/api/servers', methods=['GET', 'POST'])
@password_protected
def manage_servers():
    """Gérer les serveurs : lister (GET) ou ajouter (POST)."""
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
    """Supprimer un serveur par son ID."""
    server = db.session.get(Server, server_id)
    if not server: return jsonify({"error": "Serveur non trouvé."}), 404
    db.session.delete(server)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/options/<option_type>', methods=['GET', 'POST'])
@password_protected
def manage_options(option_type):
    """Gérer les options (saveurs/ambiance) : lister (GET) ou ajouter (POST)."""
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
        return jsonify({"id": new_option.id, "text": new_option.text, "category": getattr(new_option, 'category', None)}), 201
    options = Model.query.all()
    if option_type == 'flavors':
        return jsonify([{"id": opt.id, "text": opt.text, "category": opt.category} for opt in options])
    return jsonify([{"id": opt.id, "text": opt.text} for opt in options])

@app.route('/api/options/<option_type>/<int:option_id>', methods=['DELETE'])
@password_protected
def delete_option(option_type, option_id):
    """Supprimer une option par son type et son ID."""
    Model = FlavorOption if option_type == 'flavors' else AtmosphereOption
    option = db.session.get(Model, option_id)
    if not option: return jsonify({"error": "Option non trouvée."}), 404
    db.session.delete(option)
    db.session.commit()
    return jsonify({"success": True})

# --- ROUTES API PUBLIQUES POUR LES PAGES D'AVIS ---
# Ces routes sont accessibles sans mot de passe

@app.route('/api/public/servers')
def get_public_servers():
    """Récupère la liste des serveurs pour la page publique."""
    try:
        servers = Server.query.order_by(Server.name).all()
        return jsonify([{"name": s.name} for s in servers])
    except Exception:
        return jsonify([{"name": "Kewan (défaut)"}, {"name": "Léa (défaut)"}])

# ... (les autres routes publiques restent identiques) ...

# --- ROUTE DE GÉNÉRATION D'AVIS ET DASHBOARD ---

@app.route('/generate-review', methods=['POST'])
def generate_review():
    """Génère un avis en utilisant l'API OpenAI."""
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception:
        return jsonify({"error": "Clé API OpenAI non valide ou manquante."}), 500
    
    data = request.get_json()
    if not data: return jsonify({"error": "Données invalides."}), 400
    
    # Extraction des données de la requête
    lang = data.get('lang', 'fr')
    selected_tags = data.get('tags', [])
    prenom_serveur = "notre serveur(se)"
    service_qualities = []
    event = "une simple visite"
    liked_dishes = []
    atmosphere_notes = []

    for tag in selected_tags:
        category = tag.get('category')
        value = tag.get('value')
        if category == 'server_name': prenom_serveur = value
        elif category == 'service_qualities': service_qualities.append(value)
        elif category == 'reason_for_visit': event = value
        elif category == 'birthday_details': event += f" ({value})"
        elif category == 'liked_dishes': liked_dishes.append(value)
        elif category == 'atmosphere': atmosphere_notes.append(value)

    # Enregistre l'avis dans la base de données si un serveur est mentionné
    if prenom_serveur != "notre serveur(se)":
        try:
            new_review_record = GeneratedReview(server_name=prenom_serveur)
            db.session.add(new_review_record)
            db.session.commit()
        except Exception as e:
            print(f"Erreur d'écriture en base de données: {e}")
            db.session.rollback()

    # Construction du prompt pour OpenAI
    prompt_details = "Points que le client a aimés : "
    if service_qualities: prompt_details += f"- Le service de {prenom_serveur} était : {', '.join(service_qualities)}. "
    if liked_dishes: prompt_details += f"- Plats préférés : {', '.join(liked_dishes)}. "
    if atmosphere_notes: prompt_details += f"- Ambiance : {', '.join(atmosphere_notes)}. "
    
    system_prompt = f"""Tu es un client du restaurant italien chic Siena Paris, très satisfait, qui rédige un avis sur Google. Rédige un avis court (2-4 phrases), chaleureux et authentique. IMPORTANT : Tu dois impérativement répondre dans la langue suivante : {lang}. Mentionne impérativement le super service de "{prenom_serveur}". Intègre de manière fluide les points que le client a aimés. Si une occasion spéciale est mentionnée, intègre-la naturellement dans l'avis. Varie la formulation de chaque avis pour qu'il soit unique."""
    user_prompt = f"Contexte de la visite : {event}. Points appréciés : {prompt_details}"
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.8,
            max_tokens=150
        )
        return jsonify({"review": completion.choices[0].message.content})
    except Exception as e:
        print(f"Erreur OpenAI: {e}")
        return jsonify({"error": "Erreur lors de la génération de l'avis."}), 500

@app.route('/dashboard')
@password_protected
def dashboard_data():
    """Fournit les données pour le tableau de bord."""
    try:
        server_counts = db.session.query(
            GeneratedReview.server_name, 
            func.count(GeneratedReview.server_name).label('review_count')
        ).group_by(GeneratedReview.server_name).order_by(
            func.count(GeneratedReview.server_name).desc()
        ).all()
        results = [{"server": name, "count": count} for name, count in server_counts]
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": f"Erreur de récupération des données : {e}"}), 500

# --- INITIALISATION ET LANCEMENT ---
if __name__ == '__main__':
    with app.app_context():
        # Crée les tables si elles n'existent pas
        db.create_all() 
    # Lance le serveur de développement
    app.run(host='0.0.0.0', port=5000, debug=True)
