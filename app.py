import os
import sys
import logging
import stripe
import smtplib
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
import pytz
import uuid
import time

from flask import Flask, render_template, request, redirect, jsonify
from waitress import serve

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # This ensures logs go to stdout for Railway
        logging.StreamHandler(sys.stderr)   # Capture errors as well
    ]
)
logger = logging.getLogger(__name__)

# Ensure logger has at least one handler
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

# Stripe configuration - Switch between live and test mode
try:
    stripe_mode = os.getenv("STRIPE_MODE", "test").lower()  # Default to "test" if not set
    if stripe_mode == "live":
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")  # Live key
    else:
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY_TEST")  # Test key
    
    # Check if the key is set
    if not stripe.api_key:
        logger.error(f"Stripe API key is missing for mode: {stripe_mode.upper()}. Check environment variables.")
        sys.exit(1)
    
    logger.info(f"Stripe running in {stripe_mode.upper()} mode")
except Exception as e:
    logger.error(f"Stripe configuration error: {e}")
    sys.exit(1)

# Route for the first index page
@app.route('/')
def index():
    logger.info("Root route accessed")
    return render_template('index.html')

# Route for the second index page
@app.route('/gtmw')
def alt_index():
    return render_template('alt_index.html')  # Ensure you have an alt_index.html file in templates

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        logger.info("Checkout session creation attempted")
        base_amount = float(request.form['amount'])
        username = request.form['username']
        game = request.form['game']
        
        if base_amount < 10:
            return "Amount must be at least $10.", 400
        
        # Calculate 5% convenience fee
        convenience_fee = base_amount * 0.05
        total_amount = int(base_amount * 100)  # Stripe expects amounts in cents
        fee_amount = int(convenience_fee * 100)
        
        # Create Stripe Checkout session
        session = stripe.checkout.Session.create(
            payment_method_types=['card', 'cashapp'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"Deposit for {game}",
                        'description': f"User: {username}"
                    },
                    'unit_amount': total_amount,
                },
                'quantity': 1,
            },
            {
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Convenience Fee',
                        'description': '5% transaction fee'
                    },
                    'unit_amount': fee_amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{request.host_url}success",
            cancel_url=f"{request.host_url}cancel",
            metadata={
                'game': game,
                'username': username,
                'amount': base_amount,
                'convenience_fee': convenience_fee
            }
        )
        return redirect(session.url, code=303)
    except Exception as e:
        logger.error(f"Checkout session error: {e}")
        return jsonify({"error": "An error occurred during checkout. Please try again."}), 400

@app.route('/success')
def success():
    return render_template('success.html')

@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

# Webhook route for Stripe
@app.route('/webhook', methods=['POST'])
def stripe_webhook():

    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

    logger.info(f"Received webhook: {payload}")
    logger.info(f"Stripe-Signature: {sig_header}")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        logger.info(f"Webhook event type: {event['type']}")

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']

            # Use amount_total instead of amount_received
            amount_received = session.get('amount_total', 0) / 100  # Convert from cents

            # Extract metadata safely
            metadata = session.get('metadata', {})
            game = metadata.get('game', 'Unknown Game')
            username = metadata.get('username', 'Unknown User')
            convenience_fee = metadata.get('convenience_fee', 0.0)
            amount = metadata.get('amount', 0.0)

            # Get email from customer_details
            customer_email = session.get('customer_details', {}).get('email', None)
            if not customer_email:
                logger.error("Missing customer_email in webhook event")
                return jsonify(success=False, error="Missing customer email"), 400

            # Extract and format payment time
            payment_time_unix = session.get('created', 0)  # Get the Unix timestamp
            payment_time_utc = datetime.datetime.fromtimestamp(payment_time_unix, pytz.utc)  # Convert to UTC time

            # Convert to Central Time
            central_tz = pytz.timezone('America/Chicago')
            payment_time_cst = payment_time_utc.astimezone(central_tz)

            # Format the time in Central Time
            payment_time = payment_time_cst.strftime('%I:%M %p %Z')  # Include the timezone abbreviation (e.g., CST)

            logger.info(f"Webhook metadata: {metadata}")

            # Send email
            send_email(customer_email, amount_received, game, username, amount, convenience_fee, payment_time)

        return jsonify(success=True), 200

    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        return jsonify(success=False, error=f"Invalid payload: {e}"), 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        return jsonify(success=False, error=f"Invalid signature: {e}"), 400
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify(success=False, error=f"Webhook error: {e}"), 500

# Function to send email notifications when a payment is successful
def send_email(customer_email, amount_received, game, username, amount, convenience_fee, payment_time):
    from_email = "fkgv.load2@gmail.com"
    to_email = "fkgv.load1@gmail.com"  # Send the email to yourself (or a list of recipients)
    
    # Make the subject unique by including a unique transaction ID or timestamp
    unique_id = str(uuid.uuid4())  # Using UUID to generate a unique ID for the email
    subject = f"New Stripe Payment Received - {unique_id}"
    
    # Compose the email content using HTML for bold formatting
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
    
    # Create the email
    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    
    # Add a unique Message-ID header
    timestamp = int(time.time())  # Get the current timestamp
    msg.add_header('Message-ID', f"<{unique_id}.{timestamp}@example.com>")  # This ensures the email is unique
    
    msg.attach(MIMEText(body, 'html'))  # Set the content type to 'html'
    
    try:
        # Gmail SMTP server settings
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(from_email, os.getenv('GMAIL_APP_PASSWORD'))  # Use your Gmail app-specific password
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        logger.info(f"Email sent to {to_email}")
    except Exception as e:
        logger.error(f"Error sending email: {e}")

# Run the app using Waitress
if __name__ == "__main__":
    port = os.environ.get('PORT', 5000)
    logger.info(f"Attempting to start server on port {port}")
    
    try:
        serve(app, host="0.0.0.0", port=int(port))
    except Exception as e:
        logger.error(f"Server startup error: {e}")
        sys.exit(1)