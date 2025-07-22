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
CORS(app, supports_credentials=True)

# --- CLIENT OPENAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
database_url = os.getenv('DATABASE_URL')
if not database_url:
    raise RuntimeError("DATABASE_URL is not set. Please set it in your Render environment variables.")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
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

# --- ROUTES DE GESTION ---
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

@app.route('/api/servers/<int:server_id>', methods=['PUT', 'DELETE'])
@password_protected
def handle_server(server_id):
    server = db.session.get(Server, server_id)
    if not server:
        return jsonify({"error": "Serveur non trouvé."}), 404

    if request.method == 'PUT':
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({"error": "Nom du serveur manquant."}), 400
        server.name = data['name'].strip().title()
        db.session.commit()
        return jsonify({"id": server.id, "name": server.name})

    if request.method == 'DELETE':
        GeneratedReview.query.filter_by(server_name=server.name).delete()
        db.session.delete(server)
        db.session.commit()
        return jsonify({"success": True})


@app.route('/api/options/flavors', methods=['GET', 'POST'])
@password_protected
def manage_flavors():
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('text') or not data.get('category'):
            return jsonify({"error": "Données manquantes."}), 400
        new_option = FlavorOption(text=data['text'].strip(), category=data['category'].strip())
        db.session.add(new_option)
        db.session.commit()
        return jsonify({"id": new_option.id, "text": new_option.text, "category": new_option.category}), 201
    options = FlavorOption.query.all()
    return jsonify([{"id": opt.id, "text": opt.text, "category": opt.category} for opt in options])


@app.route('/api/options/flavors/<int:option_id>', methods=['PUT', 'DELETE'])
@password_protected
def handle_flavor(option_id):
    option = db.session.get(FlavorOption, option_id)
    if not option:
        return jsonify({"error": "Option non trouvée."}), 404

    if request.method == 'PUT':
        data = request.get_json()
        if not data or not data.get('text') or not data.get('category'):
            return jsonify({"error": "Données de l'option manquantes."}), 400
        option.text = data['text'].strip()
        option.category = data['category'].strip()
        db.session.commit()
        return jsonify({"id": option.id, "text": option.text, "category": option.category})

    if request.method == 'DELETE':
        db.session.delete(option)
        db.session.commit()
        return jsonify({"success": True})


# --- ROUTES API PUBLIQUES ---
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
            "servers": [{"id": s.id, "name": s.name} for s in servers],
            "flavors": flavors_by_category,
        }
        return jsonify(data)
    except Exception as e:
        print(f"Erreur lors de la récupération des données publiques : {e}")
        return jsonify({"error": "Impossible de charger les données de configuration."}), 500

# --- ROUTE DE GÉNÉRATION D'AVIS ---
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
        
        if server_name:
            prompt_text += f"\nL'avis doit mentionner le service impeccable de {server_name}.\n"

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

# --- ROUTES DU DASHBOARD ---
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

@app.route('/api/internal-feedback/<int:feedback_id>/status', methods=['PUT'])
@password_protected
def update_feedback_status(feedback_id):
    data = request.get_json()
    new_status = data.get('status')
    if not new_status or new_status not in ['read', 'archived', 'new']:
        return jsonify({"error": "Statut invalide."}), 400
    feedback = db.session.get(InternalFeedback, feedback_id)
    if not feedback:
        return jsonify({"error": "Feedback non trouvé."}), 404
    try:
        feedback.status = new_status
        db.session.commit()
        return jsonify({"success": True, "message": f"Feedback {feedback_id} mis à jour à '{new_status}'."})
    except Exception as e:
        db.session.rollback()
        print(f"Erreur de mise à jour du statut du feedback: {e}")
        return jsonify({"error": "Erreur lors de la mise à jour du statut."}), 500

# --- ROUTE PERFORMANCE DU MENU ---
@app.route('/api/menu-performance')
@password_protected
def menu_performance_data():
    period = request.args.get('period', 'all')
    try:
        query = db.session.query(
            MenuSelection.dish_name,
            MenuSelection.dish_category,
            func.count(MenuSelection.id).label('selection_count')
        )
        if period == '7days':
            query = query.filter(MenuSelection.selection_timestamp >= (func.now() - text("'7 days'::interval")))
        elif period == '30days':
            query = query.filter(MenuSelection.selection_timestamp >= (func.now() - text("'30 days'::interval")))
        results = query.group_by(
            MenuSelection.dish_name,
            MenuSelection.dish_category
        ).order_by(
            func.count(MenuSelection.id).desc()
        ).all()
        data = [{
            "dish_name": name,
            "dish_category": category,
            "selection_count": count
        } for name, category, count in results]
        return jsonify(data)
    except Exception as e:
        print(f"Erreur performance menu: {e}")
        return jsonify({"error": "Impossible de charger les données de performance."}), 500

# --- NOUVELLE ROUTE POUR RÉINITIALISER LES DONNÉES ---
@app.route('/api/reset-data', methods=['POST'])
@password_protected
def reset_data():
    try:
        # Utilise TRUNCATE pour vider les tables et réinitialiser les compteurs
        db.session.execute(text('TRUNCATE TABLE generated_review RESTART IDENTITY CASCADE;'))
        db.session.execute(text('TRUNCATE TABLE menu_selections RESTART IDENTITY CASCADE;'))
        db.session.commit()
        return jsonify({"success": True, "message": "Les données d'avis et de performance ont été réinitialisées."})
    except Exception as e:
        db.session.rollback()
        print(f"Erreur lors de la réinitialisation des données : {e}")
        return jsonify({"error": "Une erreur est survenue lors de la réinitialisation."}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
