import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text, desc
from sqlalchemy.orm import aliased
from datetime import datetime, timedelta
from functools import wraps

# --- CONFIGURATION INITIALE ---
load_dotenv()
# On indique à Flask que les fichiers statiques (HTML, assets) se trouvent dans un dossier nommé 'static'
app = Flask(__name__, static_folder='static')
CORS(app, supports_credentials=True)

# --- CLIENT OPENAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
database_url = os.getenv('DATABASE_URL')
if not database_url:
    raise RuntimeError("DATABASE_URL is not set.")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'siena_secret_password')
db = SQLAlchemy(app)

# --- MODÈLES DE LA BASE DE DONNÉES ---
# ... (Vos modèles restent inchangés)
class GeneratedReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    review_mode = db.Column(db.String(50), nullable=True, default='full')

class Server(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class FlavorOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)

class MenuSelection(db.Model):
    __tablename__ = 'menu_selections'
    id = db.Column(db.Integer, primary_key=True)
    dish_name = db.Column(db.Text, nullable=False)
    dish_category = db.Column(db.Text, nullable=False)
    selection_timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now())

class InternalFeedback(db.Model):
    __tablename__ = 'internal_feedback'
    id = db.Column(db.Integer, primary_key=True)
    feedback_text = db.Column(db.Text, nullable=False)
    associated_server_id = db.Column(db.Integer, db.ForeignKey('server.id', ondelete='SET NULL'), nullable=True)
    status = db.Column(db.Text, nullable=False, default='new')
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    server = db.relationship('Server')

# --- INITIALISATION DE LA BASE DE DONNÉES ---
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

# --- ROUTES POUR SERVIR LES PAGES HTML ---
# Route pour la page d'accueil (/) qui sert index.html
@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

# Route pour servir les autres fichiers (app.html, admin.html, et les assets comme les images)
# Flask est assez intelligent pour d'abord chercher les routes API spécifiques ci-dessous.
# Cette route ne s'appliquera qu'aux fichiers non trouvés par les autres routes.
@app.route('/<path:path>')
def serve_static_files(path):
    return send_from_directory(app.static_folder, path)


# --- ROUTES API (votre code existant) ---
@app.route('/api/public/data', methods=['GET'])
def get_public_data():
    # ... (code inchangé)
    try:
        servers = Server.query.order_by(Server.name).all()
        flavors = FlavorOption.query.all()
        flavors_by_category = {}
        for f in flavors:
            if f.category not in flavors_by_category:
                flavors_by_category[f.category] = []
            flavors_by_category[f.category].append({"id": f.id, "text": f.text})
        data = {
            "servers": [{"id": s.id, "name": s.name} for s in servers],
            "flavors": flavors_by_category,
        }
        return jsonify(data)
    except Exception as e:
        print(f"Erreur lors de la récupération des données publiques : {e}")
        return jsonify({"error": "Impossible de charger les données de configuration."}), 500

@app.route('/generate-review', methods=['POST'])
def generate_review():
    # ... (code inchangé)
    data = request.get_json()
    if not data: return jsonify({"error": "Données invalides."}), 400
    lang = data.get('lang', 'fr')
    tags = data.get('tags', [])
    private_feedback = data.get('private_feedback', '').strip()
    review_mode = data.get('review_mode', 'full')
    has_public_review_data = any(tag.get('category') not in ['server_name', 'reason_for_visit'] for tag in tags)
    has_private_feedback = bool(private_feedback)
    if not has_public_review_data and not has_private_feedback:
        return jsonify({"error": "Aucune donnée à traiter."}), 400
    details = {}
    dish_selections = []
    for tag in tags:
        if tag.get('category') and tag.get('value'):
            if tag['category'] not in details:
                details[tag['category']] = []
            details[tag['category']].append(tag['value'])
            if tag['category'] == 'dish':
                flavor_option = FlavorOption.query.filter_by(text=tag['value']).first()
                if flavor_option:
                    dish_selections.append({ "name": tag['value'], "category": flavor_option.category })
    server_name = details.get('server_name', [None])[0]
    if has_private_feedback:
        server_id = None
        if server_name:
            server_obj = Server.query.filter_by(name=server_name).first()
            if server_obj: server_id = server_obj.id
        new_feedback = InternalFeedback(feedback_text=private_feedback, associated_server_id=server_id)
        db.session.add(new_feedback)
    if server_name:
        new_review_log = GeneratedReview(server_name=server_name, review_mode=review_mode)
        db.session.add(new_review_log)
    for dish in dish_selections:
        new_selection = MenuSelection(dish_name=dish['name'], dish_category=dish['category'])
        db.session.add(new_selection)
    try:
        db.session.commit()
        if not has_public_review_data:
            return jsonify({"message": "Feedback enregistré avec succès."})
        prompt_text = f"Rédige un avis client positif et chaleureux pour un restaurant italien nommé Siena, en langue '{lang}'. L'avis doit sembler authentique et personnel. Incorpore les éléments suivants de manière naturelle:\n"
        for category, values in details.items():
            if category != 'server_name':
                if category == 'highlight':
                    prompt_text += f"- Points forts appréciés: {', '.join(values)}\n"
                else:
                    prompt_text += f"- {category}: {', '.join(values)}\n"
        prompt_text += "\nL'avis doit faire environ 4-6 phrases. Varie le style pour ne pas être répétitif."
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
        db.session.rollback()
        print(f"Erreur OpenAI ou DB: {e}")
        return jsonify({"error": "Désolé, une erreur est survenue lors de la génération de l'avis."}), 500

# ... (Le reste de vos routes API reste inchangé)
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
    app.run(debug=False)
