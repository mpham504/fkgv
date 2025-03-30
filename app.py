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
import time

from flask import Flask, render_template, request, redirect, jsonify, session
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
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))  # Add a secret key for session

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
            success_url=f"{request.host_url}success?session_id={{CHECKOUT_SESSION_ID}}",
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
    # Get the session_id from the URL parameter
    session_id = request.args.get('session_id')
    payment_info = {}
    
    if session_id:
        try:
            # Retrieve the checkout session to get payment info
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            # Get the payment intent ID
            payment_intent_id = checkout_session.payment_intent
            
            # Store payment details to display
            payment_info = {
                'payment_id': payment_intent_id,
                'amount': checkout_session.amount_total / 100,  # Convert from cents
                'game': checkout_session.metadata.get('game', 'Unknown Game'),
                'username': checkout_session.metadata.get('username', 'Unknown User')
            }
            logger.info(f"Payment successful, ID: {payment_intent_id}")
        except Exception as e:
            logger.error(f"Error retrieving session information: {e}")
    
    return render_template('success.html', payment_info=payment_info)

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
            
            # Get the payment intent ID
            payment_intent_id = session.get('payment_intent', 'Unknown Payment ID')

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
            payment_date = payment_time_cst.strftime('%B %d, %Y')  # Add the date for the email

            logger.info(f"Webhook metadata: {metadata}")

            # Send email with payment intent ID
            send_email(customer_email, amount_received, game, username, amount, 
                      convenience_fee, payment_time, payment_date, payment_intent_id)

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
def send_email(customer_email, amount_received, game, username, amount, convenience_fee, payment_time, payment_date, payment_id):
    from_email = "fkgv.load2@gmail.com"
    from_name = "Fire Kirin GV"  # Set your preferred display name here
    to_email = "fkgv.load1@gmail.com"  # Send the email to yourself (or a list of recipients)
    
    # Use the Stripe payment ID in the subject
    subject = f"New Payment Notification - {payment_id}"
    
    # Compose the email content using HTML with professional styling
    body = f"""
    <html>
    <head>
        <style>
            body {{
                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                color: #333333;
                line-height: 1.6;
                margin: 0;
                padding: 0;
            }}
            .email-container {{
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                background-color: #635BFF;
                color: white;
                padding: 20px;
                text-align: center;
                border-radius: 4px 4px 0 0;
            }}
            .content {{
                background-color: #ffffff;
                padding: 20px;
                border: 1px solid #e6e6e6;
                border-top: none;
                border-radius: 0 0 4px 4px;
            }}
            .info-table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 20px;
            }}
            .info-table td {{
                padding: 10px;
                border-bottom: 1px solid #e6e6e6;
            }}
            .info-table tr:last-child td {{
                border-bottom: none;
            }}
            .label {{
                color: #888888;
                width: 40%;
            }}
            .value {{
                font-weight: normal;
            }}
            .highlight {{
                font-weight: bold;
                color: #222222;
            }}
            .payment-id {{
                background-color: #f5f5f5;
                padding: 8px 12px;
                border-radius: 4px;
                font-family: monospace;
                font-size: 14px;
                color: #333333;
                display: inline-block;
                margin-top: 5px;
            }}
            .total-amount {{
                font-size: 18px;
                color: #635BFF;
                font-weight: bold;
            }}
            .footer {{
                margin-top: 20px;
                text-align: center;
                color: #888888;
                font-size: 12px;
            }}
            .action {{
                background-color: #f5f5f5;
                padding: 15px;
                border-radius: 4px;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="email-container">
            <div class="header">
                <h2 style="margin: 0;">Payment Received</h2>
            </div>
            <div class="content">
                <p>A new payment has been successfully processed.</p>
                
                <h3>Payment Details</h3>
                <table class="info-table">
                    <tr>
                        <td class="label">Payment ID</td>
                        <td class="value highlight"><div class="payment-id">{payment_id}</div></td>
                    </tr>
                    <tr>
                        <td class="label">Date</td>
                        <td class="value">{payment_date}</td>
                    </tr>
                    <tr>
                        <td class="label">Time</td>
                        <td class="value">{payment_time}</td>
                    </tr>
                    <tr>
                        <td class="label">Customer</td>
                        <td class="value">{customer_email}</td>
                    </tr>
                    <tr>
                        <td class="label">Game</td>
                        <td class="value">{game}</td>
                    </tr>
                    <tr>
                        <td class="label">Username</td>
                        <td class="value">{username}</td>
                    </tr>
                    <tr>
                        <td class="label">Deposit Amount</td>
                        <td class="value highlight">${amount}</td>
                    </tr>
                    <tr>
                        <td class="label">Convenience Fee</td>
                        <td class="value">${convenience_fee}</td>
                    </tr>
                    <tr>
                        <td class="label">Total Amount</td>
                        <td class="value total-amount">${amount_received}</td>
                    </tr>
                </table>
                
                <div class="action">
                    <p><strong>Action Required:</strong> Please load the payment and send customer confirmation as soon as possible.</p>
                </div>
            </div>
            <div class="footer">
                <p>This is an automated notification from Fire Kirin GV. Do not reply to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Create the email
    msg = MIMEMultipart()
    msg['From'] = f"{from_name} <{from_email}>"  # Adding the display name and email address
    msg['To'] = to_email
    msg['Subject'] = subject
    
    # Add the payment ID as Message-ID header
    timestamp = int(time.time())  # Get the current timestamp
    msg.add_header('Message-ID', f"<{payment_id}.{timestamp}@example.com>")
    
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