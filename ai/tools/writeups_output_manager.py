"""
Write-ups Output Manager
Handles different output formats and delivery methods for write-ups.
"""

import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
import markdown
import html2text

# Set up logging
logger = logging.getLogger(__name__)

class WriteupsOutputManager:
    """Manages output formatting and delivery for write-ups."""
    
    def __init__(self):
        """Initialize the output manager."""
        self.output_dir = self._get_output_directory()
        self._ensure_output_directory()
    
    def _get_output_directory(self) -> str:
        """Get the output directory for write-ups."""
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(script_dir, 'output', 'writeups')
        return output_dir
    
    def _ensure_output_directory(self):
        """Ensure the output directory exists."""
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            logger.debug(f"Ensured output directory exists: {self.output_dir}")
        except Exception as e:
            logger.error(f"Error creating output directory: {e}")
    
    def save_to_file(self, file_path: str, content: str, format: str = "html", 
                    writeup_id: Optional[int] = None) -> Dict[str, Any]:
        """Save write-up content to a file."""
        try:
            # If file_path is relative, make it relative to the output directory
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.output_dir, file_path)
            
            # Ensure the directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Format the content based on the specified format
            formatted_content = self._format_content(content, format)
            
            # Write the content to file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(formatted_content)
            
            logger.info(f"Saved write-up content to file: {file_path}")
            
            return {
                "status": "success",
                "file_path": file_path,
                "format": format,
                "size": len(formatted_content)
            }
            
        except Exception as e:
            logger.error(f"Error saving write-up to file: {e}")
            return {"status": "error", "message": str(e)}
    
    def save_with_timestamp(self, content: str, format: str = "html", 
                          writeup_id: Optional[int] = None, title: str = "writeup") -> Dict[str, Any]:
        """Save write-up content with a timestamped filename."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Create filename
            if writeup_id:
                filename = f"writeup_{writeup_id}_{timestamp}.{format}"
            else:
                safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_title = safe_title.replace(' ', '_')
                filename = f"{safe_title}_{timestamp}.{format}"
            
            file_path = os.path.join(self.output_dir, filename)
            
            return self.save_to_file(file_path, content, format, writeup_id)
            
        except Exception as e:
            logger.error(f"Error saving write-up with timestamp: {e}")
            return {"status": "error", "message": str(e)}
    
    def send_email(self, to: str, subject: str, content: str, format: str = "html",
                  from_email: Optional[str] = None) -> Dict[str, Any]:
        """Send write-up content via email."""
        try:
            # Get email configuration from environment
            smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
            smtp_port = int(os.getenv('SMTP_PORT', '587'))
            smtp_username = os.getenv('SMTP_USERNAME')
            smtp_password = os.getenv('SMTP_PASSWORD')
            
            if not smtp_username or not smtp_password:
                return {"status": "error", "message": "SMTP credentials not configured"}
            
            if not from_email:
                from_email = smtp_username
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = from_email
            msg['To'] = to
            msg['Subject'] = subject
            
            # Format content
            if format == "html":
                html_content = self._format_content(content, "html")
                text_content = self._html_to_text(html_content)
            else:
                text_content = self._format_content(content, "text")
                html_content = self._text_to_html(text_content)
            
            # Add both text and HTML parts
            text_part = MIMEText(text_content, 'plain', 'utf-8')
            html_part = MIMEText(html_content, 'html', 'utf-8')
            
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
            
            logger.info(f"Successfully sent email to {to}")
            
            return {
                "status": "success",
                "to": to,
                "subject": subject,
                "format": format
            }
            
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return {"status": "error", "message": str(e)}
    
    def generate_html_report(self, content: str, title: str = "Write-up", 
                           writeup_id: Optional[int] = None) -> Dict[str, Any]:
        """Generate a complete HTML report."""
        try:
            html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }}
        h2 {{
            color: #34495e;
            margin-top: 30px;
            margin-bottom: 15px;
        }}
        h3 {{
            color: #7f8c8d;
            margin-top: 25px;
            margin-bottom: 10px;
        }}
        p {{
            margin-bottom: 15px;
        }}
        ul, ol {{
            margin-bottom: 15px;
            padding-left: 30px;
        }}
        li {{
            margin-bottom: 5px;
        }}
        .metadata {{
            background-color: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 30px;
            font-size: 14px;
            color: #7f8c8d;
        }}
        .content {{
            line-height: 1.8;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #bdc3c7;
            font-size: 12px;
            color: #95a5a6;
            text-align: center;
        }}
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}
            .container {{
                padding: 20px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        
        <div class="metadata">
            <strong>Generated:</strong> {datetime.now().strftime("%B %d, %Y at %I:%M %p")}
            {f'<br><strong>Write-up ID:</strong> {writeup_id}' if writeup_id else ''}
        </div>
        
        <div class="content">
            {content}
        </div>
        
        <div class="footer">
            <p>Generated by TransparentSF Write-ups System</p>
        </div>
    </div>
</body>
</html>
            """
            
            return {
                "status": "success",
                "html": html_template,
                "title": title,
                "writeup_id": writeup_id
            }
            
        except Exception as e:
            logger.error(f"Error generating HTML report: {e}")
            return {"status": "error", "message": str(e)}
    
    def generate_markdown_report(self, content: str, title: str = "Write-up", 
                               writeup_id: Optional[int] = None) -> Dict[str, Any]:
        """Generate a markdown report."""
        try:
            timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
            
            markdown_content = f"""# {title}

**Generated:** {timestamp}
{f'**Write-up ID:** {writeup_id}' if writeup_id else ''}

---

{content}

---

*Generated by TransparentSF Write-ups System*
"""
            
            return {
                "status": "success",
                "markdown": markdown_content,
                "title": title,
                "writeup_id": writeup_id
            }
            
        except Exception as e:
            logger.error(f"Error generating markdown report: {e}")
            return {"status": "error", "message": str(e)}
    
    def generate_json_report(self, content: str, title: str = "Write-up", 
                           writeup_id: Optional[int] = None, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate a JSON report."""
        try:
            report_data = {
                "title": title,
                "content": content,
                "writeup_id": writeup_id,
                "generated_at": datetime.now().isoformat(),
                "format": "json",
                "metadata": metadata or {}
            }
            
            json_content = json.dumps(report_data, indent=2, ensure_ascii=False)
            
            return {
                "status": "success",
                "json": json_content,
                "data": report_data
            }
            
        except Exception as e:
            logger.error(f"Error generating JSON report: {e}")
            return {"status": "error", "message": str(e)}
    
    def _format_content(self, content: str, format: str) -> str:
        """Format content for the specified output format."""
        try:
            if format == "html":
                return self._ensure_html_format(content)
            elif format == "markdown":
                return self._ensure_markdown_format(content)
            elif format == "text":
                return self._html_to_text(content)
            elif format == "json":
                return json.dumps({"content": content}, indent=2)
            else:
                return content
                
        except Exception as e:
            logger.error(f"Error formatting content: {e}")
            return content
    
    def _ensure_html_format(self, content: str) -> str:
        """Ensure content is properly formatted HTML."""
        if not content.strip().startswith('<'):
            # Convert markdown to HTML if needed
            try:
                html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])
                return html_content
            except:
                # If markdown conversion fails, wrap in basic HTML
                return f"<div>{content}</div>"
        return content
    
    def _ensure_markdown_format(self, content: str) -> str:
        """Ensure content is properly formatted markdown."""
        if content.strip().startswith('<'):
            # Convert HTML to markdown if needed
            try:
                h = html2text.HTML2Text()
                h.ignore_links = False
                h.ignore_images = False
                markdown_content = h.handle(content)
                return markdown_content
            except:
                # If HTML conversion fails, return as-is
                return content
        return content
    
    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML content to plain text."""
        try:
            h = html2text.HTML2Text()
            h.ignore_links = True
            h.ignore_images = True
            text_content = h.handle(html_content)
            return text_content.strip()
        except:
            # Fallback: simple HTML tag removal
            import re
            text_content = re.sub(r'<[^>]+>', '', html_content)
            return text_content.strip()
    
    def _text_to_html(self, text_content: str) -> str:
        """Convert plain text to HTML."""
        try:
            # Convert line breaks to HTML
            html_content = text_content.replace('\n', '<br>\n')
            return f"<div>{html_content}</div>"
        except:
            return f"<div>{text_content}</div>"
    
    def get_output_files(self, writeup_id: Optional[int] = None) -> Dict[str, Any]:
        """Get list of output files."""
        try:
            files = []
            
            for filename in os.listdir(self.output_dir):
                file_path = os.path.join(self.output_dir, filename)
                
                if os.path.isfile(file_path):
                    # Check if this file belongs to the specified writeup
                    if writeup_id and f"writeup_{writeup_id}_" not in filename:
                        continue
                    
                    stat = os.stat(file_path)
                    files.append({
                        "filename": filename,
                        "path": file_path,
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
            
            # Sort by modification time (newest first)
            files.sort(key=lambda x: x["modified"], reverse=True)
            
            return {
                "status": "success",
                "files": files,
                "count": len(files)
            }
            
        except Exception as e:
            logger.error(f"Error getting output files: {e}")
            return {"status": "error", "message": str(e)}
    
    def delete_output_file(self, filename: str) -> Dict[str, Any]:
        """Delete an output file."""
        try:
            file_path = os.path.join(self.output_dir, filename)
            
            if not os.path.exists(file_path):
                return {"status": "error", "message": "File not found"}
            
            os.remove(file_path)
            logger.info(f"Deleted output file: {filename}")
            
            return {"status": "success", "message": f"Deleted {filename}"}
            
        except Exception as e:
            logger.error(f"Error deleting output file: {e}")
            return {"status": "error", "message": str(e)}
    
    def cleanup_old_files(self, days_old: int = 30) -> Dict[str, Any]:
        """Clean up old output files."""
        try:
            cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
            deleted_count = 0
            
            for filename in os.listdir(self.output_dir):
                file_path = os.path.join(self.output_dir, filename)
                
                if os.path.isfile(file_path):
                    file_time = os.path.getmtime(file_path)
                    
                    if file_time < cutoff_time:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.info(f"Deleted old output file: {filename}")
            
            return {
                "status": "success",
                "deleted_count": deleted_count,
                "days_old": days_old
            }
            
        except Exception as e:
            logger.error(f"Error cleaning up old files: {e}")
            return {"status": "error", "message": str(e)}

