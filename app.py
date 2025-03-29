import os
import sys
import logging
import stripe
import smtplib
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime

from flask import Flask, render_template, request, redirect, jsonify
from waitress import serve

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

if not logger.hasHandlers():
    logger.addHandler(logging.StreamHandler(sys.stdout))

# Load environment variables
try:
    load_dotenv()
    logger.info("Environment variables loaded successfully")
except Exception as e:
    logger.error(f"Error loading environment variables: {e}")
    sys.exit(1)

# Initialize Flask app
app = Flask(__name__)

# Stripe configuration
try:
    stripe_mode = os.getenv("STRIPE_MODE", "test").lower()
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY") if stripe_mode == "live" else os.getenv("STRIPE_SECRET_KEY_TEST")
    if not stripe.api_key:
        logger.error(f"Stripe API key is missing for mode: {stripe_mode.upper()}.")
        sys.exit(1)
    logger.info(f"Stripe running in {stripe_mode.upper()} mode")
except Exception as e:
    logger.error(f"Stripe configuration error: {e}")
    sys.exit(1)

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        logger.info(f"Webhook event type: {event['type']}")

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            amount_received = session.get('amount_total', 0) / 100
            metadata = session.get('metadata', {})
            game = metadata.get('game', 'Unknown Game')
            username = metadata.get('username', 'Unknown User')
            
            # Convert metadata values safely
            convenience_fee = round(float(metadata.get('convenience_fee', 0.0)), 2)
	    amount = round(float(metadata.get('amount', 0.0)), 2)

            customer_email = session.get('customer_details', {}).get('email')
            if not customer_email:
                logger.error("Missing customer_email in webhook event")
                return jsonify(success=False, error="Missing customer email"), 400

            # Extract and format payment time
            payment_time_unix = session.get('created', 0)
            payment_time = datetime.datetime.fromtimestamp(payment_time_unix).strftime('%I:%M %p') if payment_time_unix else "Unknown Time"
            
            logger.info(f"Webhook metadata: {metadata}")
            send_email(customer_email, amount_received, game, username, amount, convenience_fee, payment_time)

        return jsonify(success=True), 200

    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.error(f"Webhook error: {e}")
        return jsonify(success=False, error=str(e)), 400
    except Exception as e:
        logger.error(f"Unexpected webhook error: {e}")
        return jsonify(success=False, error=str(e)), 500

def send_email(customer_email, amount_received, game, username, amount, convenience_fee, payment_time):
    from_email = "fkgv.load2@gmail.com"
    to_email = "fkgv.load1@gmail.com"
    subject = "New Stripe Payment Received"
    
    body = f"""
    <html>
    <body>
        <p>New Stripe payment received!</p>
        <p><b>Payment Received At: {payment_time}</b></p>
        <p>Customer: {customer_email}</p>
        <p>Username: {username}</p>
        <p>Game: {game}</p>
        <p><b>Deposit Amount: ${amount}</b></p>
        <p>Convenience Fee: ${convenience_fee}</p>
        <p>Total Amount: ${amount_received}</p>
        <p>Please load the payment and send customer confirmation.</p>
    </body>
    </html>
    """
    
    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(from_email, os.getenv('GMAIL_APP_PASSWORD'))
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        logger.info(f"Email sent to {to_email}")
    except Exception as e:
        logger.error(f"Error sending email: {e}")

if __name__ == "__main__":
    port = os.environ.get('PORT', 5000)
    logger.info(f"Attempting to start server on port {port}")
    try:
        serve(app, host="0.0.0.0", port=int(port))
    except Exception as e:
        logger.error(f"Server startup error: {e}")
        sys.exit(1)