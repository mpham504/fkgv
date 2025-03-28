@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            
            # More robust metadata extraction with default values
            metadata = session.get('metadata', {})
            game = metadata.get('game', 'Unknown Game')
            username = metadata.get('username', 'Unknown User')
            
            # Safely convert amount and fee to float
            try:
                amount = float(metadata.get('amount', 0))
                convenience_fee = float(metadata.get('convenience_fee', 0))
            except (ValueError, TypeError):
                amount = 0
                convenience_fee = 0
            
            # Additional safety checks
            customer_email = session.get('customer_email', 'No email provided')
            amount_received = session.get('amount_total', 0) / 100  # Total amount in dollars

            # Log the details for debugging
            logger.info(f"Webhook received for {username} - Game: {game}")

            # Send detailed email
            send_email(
                customer_email, 
                amount_received, 
                game, 
                username, 
                amount, 
                convenience_fee
            )

        return jsonify(success=True), 200

    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.error(f"Webhook error: {e}")
        return jsonify(success=False, error=str(e)), 400

def send_email(customer_email, amount_received, game, username, amount, convenience_fee):
    from_email = "fkgv.load2@gmail.com"
    to_email = "fkgv.load1@gmail.com"
    subject = f"New Payment: {game} - {username}"
    
    body = f"""
    New Payment Received!

    Payment Details:
    ---------------
    Customer Email: {customer_email}
    Game: {game}
    Username: {username}

    Financial Breakdown:
    -------------------
    Base Deposit: ${amount:.2f}
    Convenience Fee: ${convenience_fee:.2f}
    Total Paid: ${amount_received:.2f}

    Please process the deposit and send customer confirmation.
    """

    try:
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(from_email, os.getenv('GMAIL_APP_PASSWORD'))
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        
        logger.info(f"Payment notification email sent for {username}")
    except Exception as e:
        logger.error(f"Error sending email: {e}")