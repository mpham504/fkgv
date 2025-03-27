import os
from dotenv import load_dotenv
import stripe
from flask import Flask, render_template, request, redirect

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
        total_amount = int((base_amount + convenience_fee) * 100)  # Convert total amount (base + fee) to cents

        # Create Stripe Checkout session with the deposit and convenience fee as separate line items
        session = stripe.checkout.Session.create(
            payment_method_types=["card", "cashapp"],  # Correct identifier for Cash App Pay
            line_items=[
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f"Deposit for {game} (User: {username})"
                        },
                        'unit_amount': int(base_amount * 100),  # Base amount in cents
                    },
                    'quantity': 1,
                },
                {
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': 'Convenience Fee (5%)'
                        },
                        'unit_amount': int(convenience_fee * 100),  # Convert convenience fee to cents
                    },
                    'quantity': 1,
                },
            ],
            mode='payment',
            success_url='http://localhost:5000/success',
            cancel_url='http://localhost:5000/cancel',
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

if __name__ == '__main__':
    port = os.environ.get('PORT', 5000)  # Use the environment variable for port, default to 5000
    app.run(debug=True, host='0.0.0.0', port=port)
