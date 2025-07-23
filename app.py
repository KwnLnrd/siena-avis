import os
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text, desc
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

# --- CONFIGURATION INITIALE ---
load_dotenv()
app = Flask(__name__)
CORS(app, supports_credentials=True)

# --- SÉCURITÉ ---
DASHBOARD_PASSWORD_HASH = os.getenv('DASHBOARD_PASSWORD_HASH')
if not DASHBOARD_PASSWORD_HASH:
    raise RuntimeError("DASHBOARD_PASSWORD_HASH n'est pas définie.")
talisman = Talisman(app, content_security_policy=None)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"], storage_uri="memory://")

# --- CLIENT OPENAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# NOUVEAU: Prompt système pour la synthèse IA configurable
AI_SYNTHESIS_PROMPT = os.getenv("AI_SYNTHESIS_PROMPT", 
    "Tu es un analyste expert pour le groupe de restaurants Siena. Analyse les feedbacks suivants (avis publics et suggestions privées) de la semaine passée. Rédige une synthèse managériale en HTML. Utilise des titres `<h4>` et des listes à puces `<ul><li>`. Sois concis et impactant. Mets en évidence les points forts, les points faibles, les suggestions récurrentes et les employés qui se sont démarqués. Ne mentionne pas que tu es une IA.")

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
database_url = os.getenv('DATABASE_URL')
if not database_url:
    raise RuntimeError("DATABASE_URL is not set.")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODÈLES DE LA BASE DE DONNÉES (MIS À JOUR) ---
class GeneratedReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

class Server(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    # NOUVEAU: Champ pour l'URL de l'image
    image_url = db.Column(db.String(255), nullable=True)

class FlavorOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)

class MenuSelection(db.Model):
    __tablename__ = 'menu_selections'
    id = db.Column(db.Integer, primary_key=True)
    dish_name = db.Column(db.Text, nullable=False)
    dish_category = db.Column(db.Text, nullable=False)
    selection_timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)

class InternalFeedback(db.Model):
    __tablename__ = 'internal_feedback'
    id = db.Column(db.Integer, primary_key=True)
    feedback_text = db.Column(db.Text, nullable=False)
    associated_server_id = db.Column(db.Integer, db.ForeignKey('server.id', ondelete='SET NULL'), nullable=True, index=True)
    status = db.Column(db.Text, nullable=False, default='new', index=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)
    server = db.relationship('Server')

class QualitativeFeedback(db.Model):
    __tablename__ = 'qualitative_feedback'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(100), nullable=False, index=True)
    value = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

# NOUVEAU: Modèle pour stocker les synthèses IA
class AiSynthesis(db.Model):
    __tablename__ = 'ai_synthesis'
    id = db.Column(db.Integer, primary_key=True)
    synthesis_html = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    start_date = db.Column(db.DateTime(timezone=True), nullable=False)
    end_date = db.Column(db.DateTime(timezone=True), nullable=False)

# --- INITIALISATION DE LA BASE DE DONNÉES ---
with app.app_context():
    db.create_all()

# --- DÉCORATEUR DE SÉCURISATION ---
def password_protected(f):
    @wraps(f)
    @limiter.limit("10 per minute")
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == 'admin' and check_password_hash(DASHBOARD_PASSWORD_HASH, auth.password)):
            return 'Accès non autorisé.', 401, {'WWW-Authenticate': 'Basic realm="Login Requis"'}
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES DE GESTION (MISES À JOUR) ---
@app.route('/api/servers', methods=['GET', 'POST'])
@password_protected
def manage_servers():
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('name'): return jsonify({"error": "Nom manquant."}), 400
        new_server = Server(
            name=data['name'].strip().title(),
            image_url=data.get('image_url', '').strip() # NOUVEAU
        )
        db.session.add(new_server)
        db.session.commit()
        return jsonify({"id": new_server.id, "name": new_server.name, "image_url": new_server.image_url}), 201
    servers = Server.query.order_by(Server.name).all()
    return jsonify([{"id": s.id, "name": s.name, "image_url": s.image_url} for s in servers])

@app.route('/api/servers/<int:server_id>', methods=['PUT', 'DELETE'])
@password_protected
def handle_server(server_id):
    server = db.session.get(Server, server_id)
    if not server: return jsonify({"error": "Serveur non trouvé."}), 404

    if request.method == 'PUT':
        data = request.get_json()
        if not data or not data.get('name'): return jsonify({"error": "Nom du serveur manquant."}), 400
        server.name = data['name'].strip().title()
        server.image_url = data.get('image_url', '').strip() # NOUVEAU
        db.session.commit()
        return jsonify({"id": server.id, "name": server.name, "image_url": server.image_url})

    if request.method == 'DELETE':
        # Gérer les dépendances avant de supprimer
        InternalFeedback.query.filter_by(associated_server_id=server.id).update({"associated_server_id": None})
        GeneratedReview.query.filter_by(server_name=server.name).delete()
        db.session.delete(server)
        db.session.commit()
        return jsonify({"success": True})

# ... (les routes de gestion des plats restent inchangées) ...

# --- ROUTE API PUBLIQUE (MISE À JOUR) ---
@app.route('/api/public/data', methods=['GET'])
def get_public_data():
    try:
        servers = Server.query.order_by(Server.name).all()
        flavors = FlavorOption.query.all()
        flavors_by_category = {}
        for f in flavors:
            if f.category not in flavors_by_category:
                flavors_by_category[f.category] = []
            flavors_by_category[f.category].append({"id": f.id, "text": f.text})
        data = {
            # NOUVEAU: renvoie aussi l'image_url
            "servers": [{"id": s.id, "name": s.name, "image_url": s.image_url} for s in servers],
            "flavors": flavors_by_category,
        }
        return jsonify(data)
    except Exception as e:
        print(f"Erreur lors de la récupération des données publiques : {e}")
        return jsonify({"error": "Impossible de charger les données de configuration."}), 500

# ... (la route /generate-review reste majoritairement inchangée) ...

# --- ROUTES DU DASHBOARD ---
# ... (les routes existantes du dashboard restent inchangées) ...

# --- NOUVELLE ROUTE POUR LA SYNTHÈSE IA ---
@app.route('/api/ai-synthesis', methods=['GET', 'POST'])
@password_protected
def ai_synthesis():
    if request.method == 'GET':
        # Récupère la synthèse la plus récente
        latest_synthesis = AiSynthesis.query.order_by(desc(AiSynthesis.created_at)).first()
        if latest_synthesis:
            return jsonify({"synthesis": latest_synthesis.synthesis_html})
        return jsonify({"synthesis": None})

    if request.method == 'POST':
        try:
            # 1. Collecter les données de la semaine passée
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=7)
            
            qualitative_feedbacks = QualitativeFeedback.query.filter(QualitativeFeedback.created_at.between(start_date, end_date)).all()
            internal_feedbacks = InternalFeedback.query.filter(InternalFeedback.created_at.between(start_date, end_date)).all()

            # 2. Formater les données pour le prompt
            feedback_data = "Feedbacks de la semaine:\n\n"
            feedback_data += "== Avis Publics (tags) ==\n"
            for qf in qualitative_feedbacks:
                feedback_data += f"- {qf.category}: {qf.value}\n"
            
            feedback_data += "\n== Suggestions Privées ==\n"
            for inf in internal_feedbacks:
                server_mention = f" (Serveur: {inf.server.name})" if inf.server else ""
                feedback_data += f"- {inf.feedback_text}{server_mention}\n"
            
            if not qualitative_feedbacks and not internal_feedbacks:
                return jsonify({"synthesis": "<h4>Aucune donnée à analyser pour la semaine passée.</h4>"})

            # 3. Appeler l'API OpenAI
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": AI_SYNTHESIS_PROMPT},
                    {"role": "user", "content": feedback_data}
                ],
                temperature=0.5,
                max_tokens=1000
            )
            synthesis_html = completion.choices[0].message.content

            # 4. Sauvegarder la synthèse en base de données
            new_synthesis = AiSynthesis(
                synthesis_html=synthesis_html,
                start_date=start_date,
                end_date=end_date
            )
            db.session.add(new_synthesis)
            db.session.commit()

            return jsonify({"synthesis": synthesis_html})

        except Exception as e:
            db.session.rollback()
            print(f"Erreur lors de la génération de la synthèse IA: {e}")
            traceback.print_exc()
            return jsonify({"error": "Une erreur est survenue lors de la génération de la synthèse."}), 500

if __name__ == '__main__':
    app.run(debug=True)
