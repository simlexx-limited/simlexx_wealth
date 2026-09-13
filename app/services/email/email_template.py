import html

BRAND_COLOR = "#0b5fff"


def render_html_email(subject: str, body: str) -> str:
    """
    Wraps a plain-text email body in a simple branded HTML template.
    Escapes the body first, so this is safe for any plain-text input.
    """
    body_html = html.escape(body).replace("\n", "<br>")
    subject_html = html.escape(subject)
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background-color:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;">
            <tr>
              <td style="background-color:{BRAND_COLOR};padding:24px 32px;">
                <span style="color:#ffffff;font-size:20px;font-weight:bold;">Simlexx Wealth</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 32px 8px;color:#1f2933;font-size:18px;font-weight:bold;">
                {subject_html}
              </td>
            </tr>
            <tr>
              <td style="padding:8px 32px 32px;color:#1f2933;font-size:15px;line-height:1.6;">
                {body_html}
              </td>
            </tr>
            <tr>
              <td style="padding:20px 32px;background-color:#f4f5f7;color:#8a94a6;font-size:12px;">
                This is an automated message from Simlexx Wealth. If you didn't request this, you can safely ignore it.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
