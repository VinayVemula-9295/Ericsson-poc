# Workspace UI refresh

The application opens on a branded demo entry screen. Enter demo workspace starts
a session as Jordan Davis, a sample platform administrator. This is presentation
access, not production authentication. Sign out clears session state, including
pending drafts and approval UI, without changing saved deployments.

The header holds the Innominds logo and product name, Configure Cloud, and the
profile avatar. Configure Cloud opens Control Center with shared provider/account
selection and an expandable connection management form. Use selected account
returns to the workspace. Infrastructure & Monitoring is available in the profile
menu. The sidebar contains Dashboard, Provisioning Agent, Deployment, and a bottom
profile. It has no Control Center navigation item.

Page content omits large repeated introductions and cloud selector cards. Dashboard
provider cards share typography and spacing; View all sits at the lower-right of
the recent provisioning card. Reuse displays successful records with Details and
Reuse actions. Missing verified bundles disable Reuse with an explanation.

Install requirements.txt to obtain streamlit-ace 0.1.1. Generated Terraform JSON
uses one editable highlighted editor. Input and code changes invalidate previous
validation and plan approval state; existing policy and execution gates remain.

Portable checks cover demo session lifecycle, successful-only reuse, source bundle
integrity, fresh-plan routing, protected configuration, and plan invalidation.
tools/check_dashboard_browser.py checks local desktop/tablet/mobile rendering,
dialog access, navigation and sign out with an isolated empty data directory and
execution disabled. Screenshots are written to .cache/dashboard-application*.png.
These checks do not establish live cloud execution or production authentication.
