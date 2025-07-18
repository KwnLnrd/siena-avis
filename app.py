import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv()
app = Flask(__name__)
CORS(app) 

# --- ROUTE DE GÉNÉRATION D'AVIS ---
@app.route('/generate-review', methods=['POST'])
def generate_review():
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception as e:
        print(f"Erreur de clé API: {e}")
        return jsonify({"error": "Clé API OpenAI non valide ou manquante."}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "Données invalides."}), 400

    lang = data.get('lang', 'fr')
    selected_tags = data.get('tags', [])
    
    # --- Construction du message pour l'IA ---
    prenom_serveur = "notre serveur(se)"
    details_anniversaire = ""
    event = "une simple visite"
    qualites_service = []
    plats_aimes = []
    notes_ambiance = []

    for tag in selected_tags:
        category = tag.get('category')
        value = tag.get('value')
        
        if category == 'server_name':
            prenom_serveur = value
        elif category == 'service_qualities':
            qualites_service.append(value)
        elif category == 'reason_for_visit':
            event = value
        elif category == 'birthday_details':
            details_anniversaire = f" (pour l'anniversaire de {value})"
        elif category == 'liked_dishes':
            plats_aimes.append(value)
        elif category == 'atmosphere':
            notes_ambiance.append(value)

    prompt_details = "Points que le client a aimés : "
    if qualites_service:
        prompt_details += f"- Le service de {prenom_serveur} était : {', '.join(qualites_service)}. "
    if plats_aimes:
        prompt_details += f"- Plats préférés : {', '.join(plats_aimes)}. "
    if notes_ambiance:
        prompt_details += f"- Ambiance : {', '.join(notes_ambiance)}. "

    # Ajoute le détail de l'anniversaire au contexte
    event += details_anniversaire

    system_prompt = f"""
    Tu es un client du restaurant italien chic Siena Paris, très satisfait, qui rédige un avis sur Google.
    Rédige un avis court (2-4 phrases), chaleureux et authentique.
    IMPORTANT : Tu dois impérativement répondre dans la langue suivante : {lang}.
    
    Mentionne impérativement le super service de "{prenom_serveur}".
    Intègre de manière fluide les points que le client a aimés.
    Si une occasion spéciale est mentionnée, intègre-la naturellement dans l'avis.
    Varie la formulation de chaque avis pour qu'il soit unique.
    """
    
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
        print(f"Erreur lors de l'appel à OpenAI: {e}")
        return jsonify({"error": "Erreur lors de la génération de l'avis."}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True)
