import os
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text, desc
from sqlalchemy.orm import aliased
from datetime import datetime, timedelta
# Importations pour JWT
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, JWTManager
from werkzeug.security import check_password_hash # On garde check_password_hash pour la sécurité
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

# --- CONFIGURATION INITIALE ---
load_dotenv()
app = Flask(__name__)
CORS(app, supports_credentials=True)

# --- CONFIGURATION DE LA SÉCURITÉ (JWT) ---
# Clé secrète pour signer les tokens JWT. Doit être gardée secrète !
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "une-super-cle-secrete-pour-le-developpement")
# Mot de passe pour le dashboard. En production, utilisez une variable d'environnement forte.
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD')
if not DASHBOARD_PASSWORD:
    raise RuntimeError("DASHBOARD_PASSWORD n'est pas définie. Veuillez la définir dans vos variables d'environnement.")

# Initialisation de JWTManager
jwt = JWTManager(app)

# Initialisation de Flask-Talisman pour les en-têtes de sécurité
talisman = Talisman(app, content_security_policy=None)

# Initialisation de Flask-Limiter pour la protection contre le brute-force
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

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
db = SQLAlchemy(app)

# --- MODÈLES DE LA BASE DE DONNÉES (inchangés) ---
class GeneratedReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

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

# --- INITIALISATION DE LA BASE DE DONNÉES ---
with app.app_context():
    db.create_all()

# --- NOUVELLE ROUTE DE LOGIN ---
@app.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute") # Limite les tentatives de connexion
def login():
    """
    Reçoit le nom d'utilisateur et le mot de passe.
    Si les identifiants sont corrects, renvoie un token d'accès JWT.
    """
    username = request.json.get("username", None)
    password = request.json.get("password", None)

    # Vérification des identifiants (simple pour cet exemple)
    if username != "admin" or password != DASHBOARD_PASSWORD:
        return jsonify({"msg": "Bad username or password"}), 401

    # Création du token d'accès
    access_token = create_access_token(identity=username)
    return jsonify(access_token=access_token)


# --- ROUTES DE GESTION (protégées par @jwt_required) ---
@app.route('/api/servers', methods=['GET', 'POST'])
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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


# --- ROUTES API PUBLIQUES (inchangées) ---
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

# --- ROUTE DE GÉNÉRATION D'AVIS (inchangée) ---
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
    
    qualitative_categories = ['service_qualities', 'atmosphere', 'reason_for_visit', 'quick_highlight']
    for tag in tags:
        category = tag.get('category')
        value = tag.get('value')
        if category in qualitative_categories and value:
            new_qualitative_feedback = QualitativeFeedback(category=category, value=value)
            db.session.add(new_qualitative_feedback)
        
        if category and value:
            if category not in details:
                details[category] = []
            details[category].append(value)
            
            if category == 'dish':
                flavor_option = FlavorOption.query.filter_by(text=value).first()
                if flavor_option:
                    dish_selections.append({ "name": value, "category": flavor_option.category })

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
        traceback.print_exc()
        return jsonify({"error": "Désolé, une erreur est survenue lors de la génération de l'avis."}), 500

# --- ROUTES DU DASHBOARD (protégées par @jwt_required) ---

@app.route('/api/server-stats')
@jwt_required()
def server_stats():
    period = request.args.get('period', 'all')
    try:
        query = db.session.query(
            GeneratedReview.server_name, 
            func.count(GeneratedReview.id).label('review_count')
        )

        if period == '7days':
            query = query.filter(GeneratedReview.created_at >= (datetime.utcnow() - timedelta(days=7)))
        elif period == '30days':
            query = query.filter(GeneratedReview.created_at >= (datetime.utcnow() - timedelta(days=30)))

        ranking_results = query.group_by(GeneratedReview.server_name).order_by(desc('review_count')).all()
        ranking_data = [{"server": server, "count": count} for server, count in ranking_results]
        return jsonify(ranking_data)
    except Exception as e:
        print(f"Erreur du dashboard (stats serveurs): {e}")
        traceback.print_exc()
        return jsonify({"error": "Impossible de charger les statistiques des serveurs."}), 500

@app.route('/dashboard')
@jwt_required()
def dashboard_data():
    period = request.args.get('period', 'all')
    try:
        base_query = GeneratedReview.query
        end_date = datetime.utcnow()
        
        days_in_period = 0
        if period == '7days':
            start_date = end_date - timedelta(days=7)
            days_in_period = 7
            base_query = base_query.filter(GeneratedReview.created_at >= start_date)
        elif period == '30days':
            start_date = end_date - timedelta(days=30)
            days_in_period = 30
            base_query = base_query.filter(GeneratedReview.created_at >= start_date)
        else:
            first_review_date = db.session.query(func.min(GeneratedReview.created_at)).scalar()
            if first_review_date:
                days_in_period = (end_date.date() - first_review_date.date()).days
            else:
                days_in_period = 0
        
        reviews_in_period = base_query.count()
        
        average_reviews_per_day = 0.0
        if days_in_period > 0:
            average_reviews_per_day = round(reviews_in_period / days_in_period, 1)
        elif reviews_in_period > 0:
            average_reviews_per_day = float(reviews_in_period)

        trend_data_dict = {}
        today = datetime.utcnow().date()
        for i in range(14):
            date = today - timedelta(days=i)
            trend_data_dict[date] = 0

        fourteen_days_ago = today - timedelta(days=13)
        
        trend_results = db.session.query(
            func.date(GeneratedReview.created_at).label('review_date'),
            func.count(GeneratedReview.id)
        ).filter(
            func.date(GeneratedReview.created_at) >= fourteen_days_ago
        ).group_by('review_date').all()

        for date, count in trend_results:
            if date in trend_data_dict:
                trend_data_dict[date] = count
        
        trend_data_list = [{"date": dt.isoformat(), "count": count} for dt, count in sorted(trend_data_dict.items())]

        final_data = {
            "stats": {
                "reviews_in_period": reviews_in_period,
                "average_reviews_per_day": average_reviews_per_day,
            },
            "trend": trend_data_list
        }
        
        return jsonify(final_data)
    except Exception as e:
        print(f"Erreur du dashboard (vue d'ensemble): {e}")
        traceback.print_exc()
        return jsonify({"error": "Impossible de charger les données de la vue d'ensemble."}), 500

@app.route('/api/qualitative-synthesis')
@jwt_required()
def qualitative_synthesis_data():
    try:
        service_qualities_query = db.session.query(
            QualitativeFeedback.value,
            func.count(QualitativeFeedback.id).label('count')
        ).filter(
            QualitativeFeedback.category == 'service_qualities'
        ).group_by(
            QualitativeFeedback.value
        ).order_by(
            desc('count')
        ).all()

        atmosphere_query = db.session.query(
            QualitativeFeedback.value,
            func.count(QualitativeFeedback.id).label('count')
        ).filter(
            QualitativeFeedback.category == 'atmosphere'
        ).group_by(
            QualitativeFeedback.value
        ).order_by(
            desc('count')
        ).all()

        service_qualities_data = [{"value": value, "count": count} for value, count in service_qualities_query]
        atmosphere_data = [{"value": value, "count": count} for value, count in atmosphere_query]

        return jsonify({
            "service_qualities": service_qualities_data,
            "atmosphere": atmosphere_data
        })
    except Exception as e:
        print(f"Erreur synthèse qualitative: {e}")
        traceback.print_exc()
        return jsonify({"error": "Impossible de charger les données de synthèse qualitative."}), 500

# NOUVELLE ROUTE POUR LA SYNTHÈSE SIF
@app.route('/api/sif-synthesis')
@jwt_required()
def sif_synthesis():
    """
    Cette route simule une analyse par IA des feedbacks pour la page SIF.
    En production, cette route contiendrait une logique complexe d'analyse de texte.
    Pour l'instant, elle renvoie des données factices structurées.
    """
    period = request.args.get('period', 'all') # Le paramètre 'period' est disponible si besoin
    
    try:
        # Ici, vous mettriez votre logique d'analyse des feedbacks
        # Pour la démonstration, nous renvoyons des données fixes.
        dummy_data = {
            "strengths": [
                "Service rapide et efficace",
                "Ambiance festive et musicale appréciée",
                "Qualité des cocktails",
                "Serveurs jugés très professionnels"
            ],
            "weaknesses": [
                "Temps d'attente parfois long le week-end",
                "Niveau sonore élevé pour certains clients",
                "Manque de certaines options végétariennes"
            ],
            "suggestions": [
                {
                    "category": "Service",
                    "suggestion": "Envisager un système de file d'attente virtuelle pour réduire la perception d'attente."
                },
                {
                    "category": "Ambiance",
                    "suggestion": "Créer des zones 'plus calmes' pour les clients désirant moins de bruit."
                },
                {
                    "category": "Menu",
                    "suggestion": "Développer 2-3 plats végétariens créatifs supplémentaires pour élargir l'offre."
                }
            ],
            "sentiment_trend": [
                {"date": (datetime.utcnow() - timedelta(days=6)).isoformat(), "score": 65},
                {"date": (datetime.utcnow() - timedelta(days=5)).isoformat(), "score": 70},
                {"date": (datetime.utcnow() - timedelta(days=4)).isoformat(), "score": 68},
                {"date": (datetime.utcnow() - timedelta(days=3)).isoformat(), "score": 75},
                {"date": (datetime.utcnow() - timedelta(days=2)).isoformat(), "score": 80},
                {"date": (datetime.utcnow() - timedelta(days=1)).isoformat(), "score": 78},
                {"date": datetime.utcnow().isoformat(), "score": 82}
            ],
            "categories": [
                {"name": "Service", "score": 85},
                {"name": "Ambiance", "score": 90},
                {"name": "Cuisine", "score": 78},
                {"name": "Propreté", "score": 95}
            ]
        }
        return jsonify(dummy_data)

    except Exception as e:
        print(f"Erreur dans /api/sif-synthesis: {e}")
        traceback.print_exc()
        return jsonify({"error": "Impossible de générer la synthèse SIF."}), 500


@app.route('/api/internal-feedback', methods=['GET'])
@jwt_required()
def get_internal_feedback():
    status_filter = request.args.get('status', 'new')
    search_term = request.args.get('search', None)
    try:
        query = db.session.query(
            InternalFeedback,
            Server.name
        ).outerjoin(
            Server, InternalFeedback.associated_server_id == Server.id
        )

        if status_filter != 'all':
            query = query.filter(InternalFeedback.status == status_filter)

        if search_term:
            query = query.filter(InternalFeedback.feedback_text.ilike(f'%{search_term}%'))

        query = query.order_by(
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
@jwt_required()
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

@app.route('/api/menu-performance')
@jwt_required()
def menu_performance_data():
    period = request.args.get('period', 'all')
    try:
        query = db.session.query(
            MenuSelection.dish_name,
            MenuSelection.dish_category,
            func.count(MenuSelection.id).label('selection_count')
        )
        if period == '7days':
            query = query.filter(MenuSelection.selection_timestamp >= (datetime.utcnow() - timedelta(days=7)))
        elif period == '30days':
            query = query.filter(MenuSelection.selection_timestamp >= (datetime.utcnow() - timedelta(days=30)))
        
        results = query.group_by(
            MenuSelection.dish_name,
            MenuSelection.dish_category
        ).order_by(
            desc('selection_count')
        ).all()

        data = [{
            "dish_name": name,
            "dish_category": category,
            "selection_count": count
        } for name, category, count in results]
        return jsonify(data)
    except Exception as e:
        print(f"Erreur performance menu: {e}")
        traceback.print_exc()
        return jsonify({"error": "Impossible de charger les données de performance."}), 500

@app.route('/api/reset-data', methods=['POST'])
@jwt_required()
def reset_data():
    try:
        db.session.execute(text('TRUNCATE TABLE generated_review, menu_selections, internal_feedback, qualitative_feedback RESTART IDENTITY CASCADE;'))
        db.session.commit()
        return jsonify({"success": True, "message": "Toutes les données de performance et d'avis ont été réinitialisées."})
    except Exception as e:
        db.session.rollback()
        print(f"Erreur lors de la réinitialisation des données: {e}")
        traceback.print_exc()
        return jsonify({"error": "Une erreur est survenue lors de la réinitialisation."}), 500

if __name__ == '__main__':
    app.run(debug=True)
