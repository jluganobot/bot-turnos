from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from supabase import create_client
import os
from datetime import datetime

app = Flask(__name__)

# ── Credenciales ──────────────────────────────────────────────
TWILIO_SID   = os.environ.get("TWILIO_SID",   "ACcd9b90ca4b5573196aaf259711a80fd3")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN", "419bd969cad797bd2e119a71124639cd")
TWILIO_FROM  = os.environ.get("TWILIO_FROM",  "whatsapp:+14155238886")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ecigltcshlgubljjjfkt.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVjaWdsdGNzaGxndWJsampqZmt0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzcyOTAxNjcsImV4cCI6MjA5Mjg2NjE2N30.z_SjeZe5mH-Nn2bQNSmyTSkbSFYN9llmCzEbgpB2eKQ")

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
supabase      = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

# ── Estado conversacional en memoria (simple, para demo) ──────
sessions = {}   # { phone: { step, name, ... } }

# ── Helper: enviar mensaje activo (para recordatorios futuros) ─
def send_whatsapp(to, body):
    twilio_client.messages.create(from_=TWILIO_FROM, to=f"whatsapp:{to}", body=body)

# ── Helper: obtener turnos libres desde Supabase ──────────────
def get_free_slots():
    if not supabase:
        return [
            {"id": 1, "fecha": "Martes 29/4", "hora": "09:00"},
            {"id": 3, "fecha": "Martes 29/4", "hora": "10:00"},
            {"id": 4, "fecha": "Miércoles 30/4", "hora": "10:00"},
        ]
    res = supabase.table("turnos").select("*").eq("estado", "libre").execute()
    return res.data

# ── Helper: reservar turno ────────────────────────────────────
def book_slot(slot_id, nombre, telefono):
    if not supabase:
        return True
    supabase.table("turnos").update({
        "estado":    "ocupado",
        "paciente":  nombre,
        "telefono":  telefono,
        "via":       "whatsapp",
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", slot_id).execute()
    return True

# ── Helper: cancelar turno por teléfono ──────────────────────
def cancel_slot(telefono):
    if not supabase:
        return None
    res = supabase.table("turnos").select("*").eq("telefono", telefono).eq("estado", "ocupado").execute()
    if not res.data:
        return None
    turno = res.data[0]
    supabase.table("turnos").update({
        "estado":    "cancelado",
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", turno["id"]).execute()
    return turno

# ── Webhook principal ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    incoming = request.form.get("Body", "").strip()
    phone    = request.form.get("From", "").replace("whatsapp:", "")
    resp     = MessagingResponse()
    msg      = resp.message()

    txt  = incoming.lower()
    sess = sessions.get(phone, {"step": "idle"})

    # ── IDLE: menú principal ──────────────────────────────────
    if sess["step"] == "idle":
        if any(w in txt for w in ["hola", "turno", "sacar", "reservar", "1"]):
            sessions[phone] = {"step": "asking_name"}
            msg.body("¡Hola! 👋 Soy el asistente del Dr. García.\n\n¿Cuál es tu nombre completo?")
        elif any(w in txt for w in ["cancel", "2"]):
            sessions[phone] = {"step": "cancel"}
            msg.body("Para cancelar tu turno, decime tu nombre completo.")
        elif any(w in txt for w in ["ver", "mis turnos", "3"]):
            res = supabase.table("turnos").select("*").eq("telefono", phone).eq("estado", "ocupado").execute() if supabase else []
            data = res.data if supabase else []
            if data:
                t = data[0]
                msg.body(f"📅 Tu turno: {t['fecha']} a las {t['hora']}\n\nPara cancelarlo respondé *cancelar*.")
            else:
                msg.body("No tenés turnos activos.\n\nEscribí *sacar turno* para reservar uno.")
        else:
            msg.body("¡Hola! 👋 ¿En qué te puedo ayudar?\n\n1. Sacar turno\n2. Cancelar turno\n3. Ver mis turnos")

    # ── ASKING_NAME ───────────────────────────────────────────
    elif sess["step"] == "asking_name":
        sessions[phone] = {"step": "choosing_slot", "name": incoming}
        slots = get_free_slots()
        if not slots:
            msg.body("Lo siento, no hay turnos disponibles por el momento. Intentá más tarde.")
            sessions[phone] = {"step": "idle"}
        else:
            lines = "\n".join([f"{i+1}. {s['fecha']} a las {s['hora']}" for i, s in enumerate(slots)])
            sessions[phone]["slots"] = slots
            msg.body(f"¡Hola {incoming}! 😊\n\nEstos turnos están disponibles:\n\n{lines}\n\n¿Cuál preferís? Respondé con el número.")

    # ── CHOOSING_SLOT ─────────────────────────────────────────
    elif sess["step"] == "choosing_slot":
        try:
            idx   = int(txt) - 1
            slots = sess.get("slots", [])
            slot  = slots[idx]
            book_slot(slot["id"], sess["name"], phone)
            sessions[phone] = {"step": "idle"}
            msg.body(
                f"✅ ¡Listo {sess['name']}! Tu turno está confirmado:\n\n"
                f"📅 {slot['fecha']}\n🕐 {slot['hora']}\n📍 Consultorio Dr. García\n\n"
                f"Te recordaremos 24hs antes. ¡Hasta pronto!"
            )
        except (ValueError, IndexError):
            slots = sess.get("slots", [])
            lines = "\n".join([f"{i+1}. {s['fecha']} a las {s['hora']}" for i, s in enumerate(slots)])
            msg.body(f"Por favor respondé con un número del 1 al {len(slots)}:\n\n{lines}")

    # ── CANCEL ────────────────────────────────────────────────
    elif sess["step"] == "cancel":
        turno = cancel_slot(phone)
        sessions[phone] = {"step": "idle"}
        if turno:
            msg.body(f"✅ Tu turno del {turno['fecha']} a las {turno['hora']} fue cancelado.\n\nEscribí *sacar turno* si querés reservar otro.")
        else:
            msg.body("No encontré turnos activos para tu número. ¿Querés sacar uno nuevo? Escribí *sacar turno*.")

    else:
        sessions[phone] = {"step": "idle"}
        msg.body("¿En qué te puedo ayudar?\n\n1. Sacar turno\n2. Cancelar turno\n3. Ver mis turnos")

    return str(resp)

@app.route("/", methods=["GET"])
def health():
    return {"status": "ok", "service": "bot-turnos-dr-garcia"}, 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
