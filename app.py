import os
import sys
import logging
from dotenv import load_dotenv
import stripe
from flask import Flask, render_template, request, redirect
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
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    if not stripe.api_key:
        logger.error("STRIPE_SECRET_KEY is not set in environment variables")
        sys.exit(1)
    logger.info("Stripe API key configured successfully")
except Exception as e:
    logger.error(f"Stripe configuration error: {e}")
    sys.exit(1)

@app.route('/')
def index():
    logger.info("Root route accessed")
    return "App is running!", 200

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
            payment_method_types=['card'],
            line_items=[
                {
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
                },
            ],
            mode='payment',
            success_url=f"{request.host_url}success",
            cancel_url=f"{request.host_url}cancel",
        )
        return redirect(session.url, code=303)
    except Exception as e:
        logger.error(f"Checkout session error: {e}")
        return str(e), 400

@app.route('/success')
def success():
    return "Payment Successful! Thank you."

@app.route('/cancel')
def cancel():
    return "Payment Canceled. Try Again."

# Run the app using Waitress
if __name__ == "__main__":
    port = os.environ.get('PORT', 5000)
    logger.info(f"Attempting to start server on port {port}")
    
    try:
        serve(app, host="0.0.0.0", port=int(port))
    except Exception as e:
        logger.error(f"Server startup error: {e}")
        sys.exit(1)