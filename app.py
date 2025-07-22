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
# Permettre les requêtes depuis votre domaine Netlify et en local pour les tests
CORS(app, resources={r"/*": {"origins": ["https://sienarestaurant.netlify.app", "http://127.0.0.1:5500", "http://localhost:5500", "null"]}})

# --- CLIENT OPENAI ---
# Assurez-vous que la variable d'environnement OPENAI_API_KEY est définie sur Render
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url.replace("postgres://", "postgresql://", 1)
else:
    # Fallback pour le développement local
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'siena_data.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Assurez-vous que la variable d'environnement DASHBOARD_PASSWORD est définie sur Render
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

# --- CORRECTION MAJEURE : INITIALISATION DE LA BASE DE DONNÉES ---
# Cette instruction s'assure que les tables sont créées au démarrage de l'application.
# C'est la correction la plus probable pour l'erreur 500 que vous rencontriez.
with app.app_context():
    db.create_all()

# --- SÉCURISATION ---
def password_protected(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == 'admin' and auth.password == DASHBOARD_PASSWORD):
            return 'Accès non autorisé.', 401, {'WWW-Authenticate': 'Basic realm="Login Requis"'}
        return f(*args, **kwargs)
    return decorated_function

# --- COMMANDE D'INITIALISATION DB (conservée pour usage manuel si besoin) ---
@app.cli.command("init-db")
def init_db_command():
    """Crée les tables et ajoute des données de base."""
    db.create_all()
    if not Server.query.first():
        db.session.add_all([Server(name='Kewan'), Server(name='Camille')])
        db.session.commit()
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
    GeneratedReview.query.filter_by(server_name=server.name).delete()
    db.session.delete(server)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/options/<option_type>', methods=['GET', 'POST'])
@password_protected
def manage_options(option_type):
    MODELS = {'flavors': FlavorOption, 'atmospheres': AtmosphereOption}
    Model = MODELS.get(option_type)
    if not Model: return jsonify({"error": "Type d'option invalide"}), 404
    
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
        result = {"id": new_option.id, "text": new_option.text}
        if option_type == 'flavors': result['category'] = new_option.category
        return jsonify(result), 201

    options = Model.query.all()
    if option_type == 'flavors':
        return jsonify([{"id": opt.id, "text": opt.text, "category": opt.category} for opt in options])
    return jsonify([{"id": opt.id, "text": opt.text} for opt in options])

@app.route('/api/options/<option_type>/<int:option_id>', methods=['DELETE'])
@password_protected
def delete_option(option_type, option_id):
    MODELS = {'flavors': FlavorOption, 'atmospheres': AtmosphereOption}
    Model = MODELS.get(option_type)
    if not Model: return jsonify({"error": "Type d'option invalide"}), 404
    
    option = db.session.get(Model, option_id)
    if not option: return jsonify({"error": "Option non trouvée."}), 404
    db.session.delete(option)
    db.session.commit()
    return jsonify({"success": True})

# --- NOUVEAU : ROUTES API PUBLIQUES POUR LA PAGE D'AVIS (app.html) ---
# Ces routes permettent à la page de génération d'avis de charger les options
# dynamiquement sans nécessiter de mot de passe.
@app.route('/api/public/data', methods=['GET'])
def get_public_data():
    try:
        servers = Server.query.order_by(Server.name).all()
        flavors = FlavorOption.query.all()
        atmospheres = AtmosphereOption.query.all()

        # Regrouper les saveurs par catégorie pour un affichage facile
        flavors_by_category = {}
        for f in flavors:
            if f.category not in flavors_by_category:
                flavors_by_category[f.category] = []
            flavors_by_category[f.category].append({"id": f.id, "text": f.text})

        data = {
            "servers": [{"id": s.id, "name": s.name} for s in servers],
            "flavors": flavors_by_category,
            "atmospheres": [{"id": a.id, "text": a.text} for a in atmospheres]
        }
        return jsonify(data)
    except Exception as e:
        print(f"Erreur lors de la récupération des données publiques : {e}")
        return jsonify({"error": "Impossible de charger les données de configuration."}), 500


# --- ROUTE DE GÉNÉRATION D'AVIS ---
@app.route('/generate-review', methods=['POST'])
def generate_review():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Données invalides."}), 400
        
    lang = data.get('lang', 'fr')
    tags = data.get('tags', [])

    details = {}
    for tag in tags:
        if tag.get('category') and tag.get('value'):
            if tag['category'] not in details:
                details[tag['category']] = []
            details[tag['category']].append(tag['value'])
    
    server_name = details.get('server_name', [None])[0]

    if server_name:
        new_review_log = GeneratedReview(server_name=server_name)
        db.session.add(new_review_log)
        db.session.commit()
    
    prompt_text = f"Rédige un avis client positif et chaleureux pour un restaurant italien nommé Siena, en langue '{lang}'. L'avis doit sembler authentique et personnel. Incorpore les éléments suivants de manière naturelle:\n"
    for category, values in details.items():
        # Ne pas inclure le nom du serveur dans le prompt pour éviter la redondance
        if category != 'server_name':
            prompt_text += f"- {category}: {', '.join(values)}\n"
    prompt_text += "\nL'avis doit faire environ 4-6 phrases. Varie le style pour ne pas être répétitif."

    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Tu es un assistant de rédaction spécialisé dans les avis de restaurants."},
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.7,
            max_tokens=200
        )
        review = completion.choices[0].message.content
        return jsonify({"review": review.strip()})
    except Exception as e:
        print(f"Erreur OpenAI: {e}")
        return jsonify({"error": "Désolé, une erreur est survenue lors de la génération de l'avis."}), 500

# --- ROUTE DU DASHBOARD ---
@app.route('/dashboard')
@password_protected
def dashboard_data():
    try:
        results = db.session.query(
            GeneratedReview.server_name, 
            func.count(GeneratedReview.id).label('review_count')
        ).group_by(GeneratedReview.server_name).order_by(func.count(GeneratedReview.id).desc()).all()
        
        data = [{"server": server, "count": count} for server, count in results]
        return jsonify(data)
    except Exception as e:
        print(f"Erreur du dashboard: {e}")
        return jsonify({"error": "Impossible de charger les données du dashboard."}), 500

# --- POINT D'ENTRÉE POUR RENDER ---
if __name__ == '__main__':
    # Le port est géré par gunicorn sur Render, debug=False en production
    app.run(debug=False)

