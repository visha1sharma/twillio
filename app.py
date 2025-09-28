# app.py
import os
import json
from datetime import datetime
from dotenv import load_dotenv

from flask import Flask, request, jsonify, abort, url_for
from flask_sqlalchemy import SQLAlchemy

from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse


# Load local .env only if present (safe for dev)
load_dotenv()

# Twilio credentials (from Render ENV or .env locally)
ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")

print("ACCOUNT_SID",ACCOUNT_SID)
print("AUTH_TOKEN",AUTH_TOKEN)
print("TWILIO_NUMBER",TWILIO_NUMBER)
if not (ACCOUNT_SID and AUTH_TOKEN and TWILIO_NUMBER):
    raise RuntimeError(
        "Missing Twilio credentials: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_NUMBER "
        "either in a .env (local) or Render Environment Variables (production)"
    )


client = Client(ACCOUNT_SID, AUTH_TOKEN)

# Flask app and DB
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///sms.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# DB model
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sid = db.Column(db.String(64), index=True, nullable=True)
    from_number = db.Column(db.String(32))
    to_number = db.Column(db.String(32))
    body = db.Column(db.Text)
    direction = db.Column(db.String(10))  # 'inbound' or 'outbound'
    status = db.Column(db.String(32), default="received")
    error_code = db.Column(db.String(32), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Send SMS endpoint
@app.route("/send-sms", methods=["POST"])
def send_sms():
    data = request.get_json(force=True)
    to = data.get("to")
    body = data.get("message")
    if not to or not body:
        return jsonify({"error": "missing 'to' or 'message'"}), 400

    # Status callback URL for delivery updates
    status_cb = url_for("status_callback", _external=True)
    message = client.messages.create(
        body=body, from_=TWILIO_NUMBER, to=to, status_callback=status_cb
    )

    # Save outbound message
    db_msg = Message(
        sid=message.sid,
        from_number=TWILIO_NUMBER,
        to_number=to,
        body=body,
        direction="outbound",
        status=message.status,
    )
    db.session.add(db_msg)
    db.session.commit()

    return jsonify({"status": "queued", "sid": message.sid}), 200

# Receive SMS endpoint
@app.route("/receive-sms", methods=["GET", "POST"])
def receive_sms():
    payload = request.form.get("Payload") 
    from_number = None
    to_number = None
    body = None

    if payload: 
        try: 
            payload_data = json.loads(payload)
            msg_data = payload_data.get("webhook", {}).get("request", {}).get("parameters", {})
            from_number = msg_data.get("From")
            to_number = msg_data.get("To")
            body = msg_data.get("Body") or msg_data.get("SmsBody")
        except Exception as e: 
            print("❌ Error parsing payload:", e) 

    # If parsing failed or payload wasn't present, fallback to regular form fields
    if not from_number:
        from_number = request.form.get("From")  # You had "Form" — typo?
    if not to_number:
        to_number = request.form.get("To")
    if not body:
        body = request.form.get("Body")

    # Debug log
    print(f"📩 Incoming SMS from {from_number}: {body}")

    # Save to DB (assuming Message and db are correctly defined)
    db_msg = Message( 
        sid=None, 
        from_number=from_number, 
        to_number=to_number, 
        body=body, 
        direction="inbound", 
        status="received", 
    )
    db.session.add(db_msg) 
    db.session.commit() 
    print("✅ Saved inbound SMS to DB.")

    # Return TwiML response
    resp = MessagingResponse()
    resp.message("✅ Thanks — we received your message.")
    return str(resp), 200, {'Content-Type': 'application/xml'}


# Status callback endpoint
@app.route("/sms/status", methods=["POST"])
def status_callback():
    sid = request.form.get("MessageSid")
    status = request.form.get("MessageStatus")
    error_code = request.form.get("ErrorCode")

    msg = Message.query.filter_by(sid=sid).first()
    if msg:
        msg.status = status
        msg.error_code = error_code
        db.session.commit()
        print(f"📤 Updated message {sid} status: {status}")

    return ("", 204)

# View all messages
@app.route("/messages", methods=["GET"])
def get_all_messages():
    msgs = Message.query.order_by(Message.timestamp.desc()).all()
    data = []
    for m in msgs:
        data.append({
            "id": m.id,
            "sid": m.sid,
            "from_number": m.from_number,
            "to_number": m.to_number,
            "body": m.body,
            "direction": m.direction,
            "status": m.status,
            "error_code": m.error_code,
            "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        })
    return jsonify(data), 200

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0",port=5000, debug=True)





