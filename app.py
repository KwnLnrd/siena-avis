import os
from flask import Flask, request, jsonify
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
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["https://sienarestaurant.netlify.app", "http://127.0.0.1:5500", "http://localhost:5500", "null"]}})

# --- CLIENT OPENAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
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

# CORRECTION : Le nom de la table est géré par défaut par SQLAlchemy, ce qui correspond à son état fonctionnel.
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
    # La clé étrangère pointe vers 'server.id', le nom de table par défaut pour le modèle Server.
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

# --- ROUTES DE GESTION ---
# ... (les routes de gestion existantes restent inchangées) ...

# --- ROUTE DE GÉNÉRATION D'AVIS (MISE À JOUR) ---
@app.route('/generate-review', methods=['POST'])
def generate_review():
    data = request.get_json()
    if not data: return jsonify({"error": "Données invalides."}), 400
        
    lang = data.get('lang', 'fr')
    tags = data.get('tags', [])
    private_feedback = data.get('private_feedback', '').strip()

    has_public_review_data = any(tag.get('category') not in ['server_name', 'reason_for_visit'] for tag in tags) or len(tags) > 1
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
        new_review_log = GeneratedReview(server_name=server_name)
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

# --- ENDPOINTS DE GESTION DU FEEDBACK ---

@app.route('/api/internal-feedback', methods=['GET'])
@password_protected
def get_internal_feedback():
    status_filter = request.args.get('status', 'new')
    
    try:
        query = db.session.query(
            InternalFeedback,
            Server.name
        ).outerjoin(
            Server, InternalFeedback.associated_server_id == Server.id
        ).filter(
            InternalFeedback.status == status_filter
        ).order_by(
            desc(InternalFeedback.created_at)
        )

        results = query.all()

        feedbacks = []
        for feedback, server_name in results:
            feedbacks.append({
                "id": feedback.id,
                "feedback_text": feedback.feedback_text,
                "status": feedback.status,
                "created_at": feedback.created_at.isoformat(),
                "server_name": server_name if server_name else "Non spécifié"
            })
            
        return jsonify(feedbacks)
    except Exception as e:
        print(f"Erreur dans /api/internal-feedback: {e}")
        return jsonify({"error": "Impossible de charger les feedbacks."}), 500

# ... (le reste des routes reste inchangé) ...
