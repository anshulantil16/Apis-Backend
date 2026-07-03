from celery import shared_task
from .models import OfferLetter, PMSEmployee
from .offer_letter import generate_offer_letter_pdf, send_offer_letter_email
from django.core.files.base import ContentFile
from datetime import datetime

@shared_task
def process_offer_letter(offer_letter_id, send_email=False):
    """Process a single offer letter: generate PDF and optionally send email."""
    try:
        offer = OfferLetter.objects.get(id=offer_letter_id)
        emp = offer.employee

        # Generate PDF
        pdf_buffer = generate_offer_letter_pdf(
            employee=emp,
            current_ctc=offer.current_ctc,
            new_ctc=offer.new_ctc,
            increment_pct=offer.increment_pct,
            promotion_pct=offer.promotion_pct,
            effective_date=offer.effective_date,
            old_designation=offer.old_designation,
            new_designation=offer.new_designation,
            performance_rating=offer.performance_rating,
            grade_label=offer.grade_label,
        )

        # Save PDF
        pdf_filename = f'{emp.employee_id}_{offer.effective_date.strftime("%Y%m%d")}.pdf'
        offer.pdf_file.save(pdf_filename, ContentFile(pdf_buffer.getvalue()))
        offer.status = 'generated'
        offer.save()

        # Send email if requested
        if send_email:
            try:
                send_offer_letter_email(offer.email_address, emp.name, pdf_buffer, offer.effective_date)
                offer.email_sent = True
                offer.email_sent_at = datetime.now()
                offer.status = 'sent'
                offer.save()
                return {'status': 'sent', 'message': 'Letter generated and sent'}
            except Exception as e:
                offer.status = 'generated_no_email'
                offer.save()
                return {'status': 'generated_no_email', 'message': f'Letter generated but email failed: {str(e)}'}
        else:
            return {'status': 'generated', 'message': 'Letter generated (email not sent)'}

    except Exception as e:
        try:
            offer = OfferLetter.objects.get(id=offer_letter_id)
            offer.status = 'failed'
            offer.save()
        except:
            pass
        return {'status': 'failed', 'message': str(e)}
