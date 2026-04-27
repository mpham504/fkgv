import os
import sys
import logging
import stripe
from dotenv import load_dotenv
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

        # IMMEDIATELY RESPOND TO STRIPE
        response = jsonify(success=True), 200

        if event['type'] == 'checkout.session.completed':
            # Process the webhook in the background
            import threading
            thread = threading.Thread(target=process_webhook_event, args=(event,))
            thread.daemon = True
            thread.start()
            logger.info("Webhook processing started in background thread")

        return response

    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        return jsonify(success=False, error=f"Invalid payload: {e}"), 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        return jsonify(success=False, error=f"Invalid signature: {e}"), 400
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify(success=False, error=f"Webhook error: {e}"), 500


# New function to process webhook events in the background
def process_webhook_event(event):
    """Process webhook event asynchronously"""
    try:
        session = event['data']['object']

        # Use amount_total instead of amount_received
        amount_received = session.get('amount_total', 0) / 100

        # Extract metadata safely
        metadata = session.get('metadata', {})
        game = metadata.get('game', 'Unknown Game')
        username = metadata.get('username', 'Unknown User')
        convenience_fee = metadata.get('convenience_fee', 0.0)
        amount = metadata.get('amount', 0.0)
        
        payment_intent_id = session.get('payment_intent', 'Unknown Payment ID')
        
        # Retrieve payment method details
        payment_method_type = "Unknown"
        card_last4 = None
        card_brand = None
        cashapp_cashtag = None
        
        try:
            if payment_intent_id and payment_intent_id != 'Unknown Payment ID':
                payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
                
                if payment_intent.get('payment_method'):
                    payment_method = stripe.PaymentMethod.retrieve(payment_intent.payment_method)
                    payment_method_type = payment_method.type
                    
                    if payment_method_type == 'card' and hasattr(payment_method, 'card'):
                        card_details = payment_method.card
                        card_last4 = card_details.get('last4', '')
                        card_brand = card_details.get('brand', '').lower()
                    
                    elif payment_method_type == 'cashapp' and hasattr(payment_method, 'cashapp'):
                        cashapp_details = payment_method.cashapp
                        cashapp_cashtag = cashapp_details.get('cashtag', '')
                
                elif hasattr(payment_intent, 'charges') and payment_intent.charges.data:
                    latest_charge = payment_intent.charges.data[0]
                    payment_method_details = latest_charge.get('payment_method_details', {})
                    payment_method_type = payment_method_details.get('type', 'Unknown')
                    
                    if payment_method_type == 'card':
                        card_details = payment_method_details.get('card', {})
                        card_last4 = card_details.get('last4', '')
                        card_brand = card_details.get('brand', '').lower()
                        
                    elif payment_method_type == 'cashapp':
                        cashapp_details = payment_method_details.get('cashapp', {})
                        cashapp_cashtag = cashapp_details.get('cashtag', '')
                
                logger.info(f"Payment method retrieved: {payment_method_type}")
        except Exception as e:
            logger.error(f"Error retrieving payment method details: {e}")

        # Get email from customer_details
        customer_email = session.get('customer_details', {}).get('email', None)
        if not customer_email:
            logger.error("Missing customer_email in webhook event")
            return

        # Extract and format payment time
        payment_time_unix = session.get('created', 0)
        payment_time_utc = datetime.datetime.fromtimestamp(payment_time_unix, pytz.utc)
        central_tz = pytz.timezone('America/Chicago')
        payment_time_cst = payment_time_utc.astimezone(central_tz)
        payment_time = payment_time_cst.strftime('%I:%M %p %Z')
        payment_date = payment_time_cst.strftime('%B %d, %Y')

        logger.info(f"Webhook metadata: {metadata}")

        # Send email
        send_email(customer_email, amount_received, game, username, amount, 
                  convenience_fee, payment_time, payment_date, payment_intent_id,
                  payment_method_type, card_brand, card_last4, cashapp_cashtag)
        
        logger.info(f"Background webhook processing completed for payment: {payment_intent_id}")
        
    except Exception as e:
        logger.error(f"Error in background webhook processing: {e}")

# Function to send email notifications when a payment is successful
def send_email(customer_email, amount_received, game, username, amount, convenience_fee, 
              payment_time, payment_date, payment_id, payment_method_type="Unknown", 
              card_brand=None, card_last4=None, cashapp_cashtag=None):
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Content
    
    from_email = Email("noreply@fkgvload.cfd", "Fire Kirin GV")
    to_email = To("fkgv.load1@gmail.com")
    subject = f"New Payment Notification - {payment_id}"
    
    # Create payment method display HTML
    payment_method_html = ""
    
    if payment_method_type == "card" and card_brand and card_last4:
        card_logo_html = ""
        if card_brand == "visa":
            card_logo_html = '<span style="font-weight: bold; color: #1434CB; margin-right: 5px;">VISA</span>'
        elif card_brand == "mastercard":
            card_logo_html = '<span style="font-weight: bold; color: #EB001B; margin-right: 5px;">MASTERCARD</span>'
        elif card_brand == "amex":
            card_logo_html = '<span style="font-weight: bold; color: #2E77BC; margin-right: 5px;">AMEX</span>'
        elif card_brand == "discover":
            card_logo_html = '<span style="font-weight: bold; color: #FF6000; margin-right: 5px;">DISCOVER</span>'
        else:
            card_logo_html = f'<span style="font-weight: bold; margin-right: 5px;">{card_brand.upper()}</span>'
        
        payment_method_html = f"{card_logo_html} •••• {card_last4}"
    
    elif payment_method_type == "cashapp" and cashapp_cashtag:
        clean_cashtag = cashapp_cashtag
        if clean_cashtag.startswith('$'):
            clean_cashtag = clean_cashtag[1:]
            
        payment_method_html = f'<span style="font-weight: bold; color: #00D632; margin-right: 5px;">CASH APP</span> ${clean_cashtag}'
    
    elif payment_method_type == "apple_pay":
        payment_method_html = '<span style="font-weight: bold; color: #000000; margin-right: 5px;">APPLE PAY</span>'
    
    elif payment_method_type == "google_pay":
        payment_method_html = '<span style="font-weight: bold; color: #4285F4; margin-right: 5px;">GOOGLE PAY</span>'
    
    else:
        payment_method_html = payment_method_type.replace('_', ' ').title()
    
    # HTML email body
    html_content = f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
                table-layout: fixed;
            }}
            .info-table td {{
                padding: 10px;
                border-bottom: 1px solid #e6e6e6;
                word-wrap: break-word;
                word-break: break-word;
            }}
            .info-table tr:last-child td {{
                border-bottom: none;
            }}
            .label {{
                color: #888888;
                width: 40%;
                vertical-align: top;
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
                padding: 8px;
                border-radius: 4px;
                font-family: monospace;
                font-size: 13px;
                color: #333333;
                display: inline-block;
                margin-top: 5px;
                word-break: break-all;
                max-width: 100%;
                box-sizing: border-box;
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
            
            @media screen and (max-width: 480px) {{
                .info-table, .info-table tbody, .info-table tr, .info-table td {{
                    display: block;
                    width: 100%;
                    box-sizing: border-box;
                }}
                .info-table td.label {{
                    border-bottom: none;
                    padding-bottom: 0;
                }}
                .info-table td.value {{
                    padding-top: 5px;
                }}
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
                        <td class="value highlight">
                            <div class="payment-id">{payment_id}</div>
                        </td>
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
                        <td class="label"><strong>Deposit Amount</strong></td>
                        <td class="value" style="font-weight: bold; font-size: 1.2em;">${amount}</td>
                    </tr>
                    <tr>
                        <td class="label">Convenience Fee</td>
                        <td class="value">${convenience_fee}</td>
                    </tr>
                    <tr>
                        <td class="label">Total Amount</td>
                        <td class="value total-amount">${amount_received}</td>
                    </tr>
                    <tr>
                        <td class="label">Payment Method</td>
                        <td class="value">{payment_method_html}</td>
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
    
    try:
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            html_content=html_content
        )
        
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        response = sg.send(message)
        logger.info(f"Email sent successfully to {to_email.email}. Status code: {response.status_code}")
        
    except Exception as e:
        logger.error(f"Error sending email via SendGrid: {e}")

# Run the app using Waitress
if __name__ == "__main__":
    port = os.environ.get('PORT', 5000)
    logger.info(f"Attempting to start server on port {port}")
    
    try:
        serve(app, host="0.0.0.0", port=int(port))
    except Exception as e:
        logger.error(f"Server startup error: {e}")
        sys.exit(1)