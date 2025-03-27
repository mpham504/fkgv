import os
from dotenv import load_dotenv
import stripe
from flask import Flask, render_template, request, redirect
from waitress import serve

load_dotenv()  # Load the environment variables from .env file

app = Flask(__name__)

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')  # Get the Stripe key from .env

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        base_amount = float(request.form['amount'])
        username = request.form['username']
        game = request.form['game']

        if base_amount < 10:
            return "Amount must be at least $10.", 400

        # Calculate the 5% convenience fee
        convenience_fee = base_amount * 0.05
        total_amount = int(base_amount * 100)  # Convert base amount to cents for Stripe

        # Get the base URL dynamically (works both in dev and production)
        base_url = request.host_url

        # Create Stripe Checkout session with the deposit and convenience fee as separate line items
        session = stripe.checkout.Session.create(
            payment_method_types=["card", "cashapp"],
            line_items=[
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f"Deposit for {game} (User: {username})"
                        },
                        'unit_amount': total_amount,
                    },
                    'quantity': 1,
                },
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': 'Convenience Fee (5%)'
                        },
                        'unit_amount': int(convenience_fee * 100),
                    },
                    'quantity': 1,
                },
            ],
            mode='payment',
            success_url=f"{base_url}success",
            cancel_url=f"{base_url}cancel",
        )

        return redirect(session.url, code=303)

    except Exception as e:
        return str(e), 400

@app.route('/success')
def success():
    return "Payment Successful! Thank you."

@app.route('/cancel')
def cancel():
    return "Payment Canceled. Try Again."

# Use Waitress to serve the app and listen on the correct port
if __name__ == "__main__":
    port = os.environ.get('PORT', 5000)  # Railway's environment variable for the port
    serve(app, host="0.0.0.0", port=int(port))