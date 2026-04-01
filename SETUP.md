# GitHub Actions Setup for Trip Monitoring

This repository monitors trip prices on Intrepid Travel and sends alerts when prices drop or availability is low.

## Required GitHub Secrets

You need to configure the following secrets in your GitHub repository settings:

**Settings → Secrets and variables → Actions → New repository secret**

### Email Configuration (SMTP)

| Secret Name | Description | Example |
|------------|-------------|---------|
| `EMAIL_TO` | Your email address to receive alerts | `you@example.com` |
| `EMAIL_FROM` | Email address to send from (optional) | `alerts@example.com` |
| `SMTP_HOST` | SMTP server hostname | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP server port (optional, defaults to 587) | `587` |
| `SMTP_USER` | SMTP username | `your-email@gmail.com` |
| `SMTP_PASS` | SMTP password or app password | `your-app-password` |

### SMTP Provider Examples

#### Gmail
- **SMTP_HOST**: `smtp.gmail.com`
- **SMTP_PORT**: `587`
- **SMTP_USER**: Your Gmail address
- **SMTP_PASS**: [App password](https://support.google.com/accounts/answer/185833) (not your regular password)

#### Outlook/Hotmail
- **SMTP_HOST**: `smtp-mail.outlook.com`
- **SMTP_PORT**: `587`
- **SMTP_USER**: Your Outlook email
- **SMTP_PASS**: Your Outlook password

#### SendGrid
- **SMTP_HOST**: `smtp.sendgrid.net`
- **SMTP_PORT**: `587`
- **SMTP_USER**: `apikey`
- **SMTP_PASS**: Your SendGrid API key

## Workflow Schedule

The workflow runs **twice daily** at:
- 9:00 AM UTC
- 9:00 PM UTC

To adjust the schedule, edit the cron expression in [`.github/workflows/monitor-trip.yml`](.github/workflows/monitor-trip.yml):

```yaml
schedule:
  - cron: '0 9,21 * * *'
```

[Learn more about cron syntax](https://crontab.guru/)

## Monitoring Configuration

The current trip being monitored:
- **URL**: https://www.intrepidtravel.com/us/morocco/essential-morocco-166485
- **Start Date**: September 3, 2026
- **End Date**: September 14, 2026

### Alert Conditions
You'll receive an alert when:
1. **Price decreases** below the previously recorded price
2. **Only 3 or fewer spots** are available

### Notification Methods
1. **Email**: Sent via SMTP to your configured email address
2. **GitHub Issue**: Created automatically with label `trip-alert`

## Manual Testing

You can manually trigger the workflow:
1. Go to **Actions** tab in your repository
2. Click **Monitor Trip Prices** workflow
3. Click **Run workflow** button

## Troubleshooting

### No emails received
- Verify SMTP credentials in GitHub secrets
- Check spam/junk folder
- For Gmail, ensure "Less secure app access" is enabled or use an app password
- Check workflow logs in Actions tab

### Workflow fails
- Check the Actions tab for error logs
- Ensure all required secrets are set
- Verify the trip URL is still valid

### State persistence
The workflow uses GitHub Actions cache to persist the `trip_state.json` file between runs. This tracks:
- Last known price
- Last known spot availability
- Last update timestamp

## Local Testing

To test the script locally:

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set environment variables
export TRIP_URL="https://www.intrepidtravel.com/us/morocco/essential-morocco-166485"
export TARGET_START="2026-09-03"
export TARGET_END="2026-09-14"
export EMAIL_TO="your-email@example.com"
export SMTP_HOST="smtp.gmail.com"
export SMTP_USER="your-email@gmail.com"
export SMTP_PASS="your-app-password"

# Run the monitor
python monitor_trip.py
```
