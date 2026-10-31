from app.services.email.tasks import queue_email


def send_profile_review_email(to_address: str, first_name: str, approved: bool, comment: str) -> None:
    """Tell a client the outcome of their profile review, with the reviewer's comment."""
    if approved:
        subject = "Your Simlexx Wealth profile was approved"
        body = (
            f"Hi {first_name},\n\n"
            "Good news: your profile has been approved.\n\n"
            f"Reviewer's comment: {comment}"
        )
    else:
        subject = "Your Simlexx Wealth profile needs changes"
        body = (
            f"Hi {first_name},\n\n"
            "Your profile was not approved.\n\n"
            f"Reason: {comment}\n\n"
            "You can correct your details or documents in the app. Your profile will "
            "then be reviewed again."
        )
    queue_email(to_address, subject, body)
