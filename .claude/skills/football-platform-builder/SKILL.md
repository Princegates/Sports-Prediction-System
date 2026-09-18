---
name: football-platform-builder
description: Build and commercialize a football-only AI prediction platform around an already-trained prediction engine — a futuristic landing page, authentication, an external-payment access-code system (no in-app payment gateway), Super Admin management, and secure integration with the existing model. Use when asked to turn this project into a commercial-ready football intelligence product.
---

# Football AI Platform Builder

## ROLE

Act as a senior product designer, frontend engineer, backend engineer, security architect, and SaaS platform specialist.

Your responsibility is to transform the existing AI football prediction application into a premium, commercial-ready football intelligence platform.

The AI prediction model has already been trained.

Do not retrain, replace, or redesign the prediction model unless explicitly instructed.

The main objective is to build an exceptional user experience and a secure access-management system around the existing AI engine.

## 1. PROJECT AUDIT — REQUIRED FIRST STEP

Before writing or modifying code:

1. Inspect the entire repository.
2. Identify the frontend framework and styling system.
3. Identify the backend architecture.
4. Identify the database and schema.
5. Identify the authentication system.
6. Locate the trained AI model and its inference interface.
7. Identify how predictions are currently generated.
8. Identify existing APIs and routes.
9. Identify existing user and admin functionality.
10. Identify available football data sources.
11. Identify existing animation libraries.
12. Identify the project's deployment configuration.

Create a short technical audit containing:

- Existing architecture
- Existing prediction-engine integration
- Existing authentication
- Existing database
- Existing UI
- Missing features
- Risks
- Recommended implementation plan

Do not replace working functionality unnecessarily.

Do not create mock functionality where a real implementation is required.

## 2. PRODUCT SCOPE

This platform supports FOOTBALL ONLY.

Do not build interfaces for:

- Basketball
- Tennis
- Baseball
- Hockey
- Cricket
- American football
- Other sports

The platform should be positioned as an AI-powered football intelligence and prediction service.

It should communicate:

- Football analytics
- Statistical intelligence
- Match analysis
- Data-driven insights
- Probability-based predictions
- Professional sports technology

Avoid making the interface resemble a casino or generic betting website.

Do not claim guaranteed outcomes, guaranteed winnings, or 98% prediction accuracy unless such claims are independently supported by a clearly documented and validated evaluation.

## 3. COMMERCIAL ACCESS MODEL

The platform must NOT contain an integrated payment gateway.

Payment takes place outside the platform.

The intended business process is:

```
CUSTOMER
  ↓ Pays through an external business channel
  ↓ Super Admin confirms payment
  ↓ Super Admin creates an access code
  ↓ Customer receives the access code
  ↓ Customer registers or logs in
  ↓ Customer redeems the access code
  ↓ The system validates the code
  ↓ The system grants access for a defined period
  ↓ Customer uses the football prediction platform
  ↓ Access expires automatically
```

Do not implement:

- Stripe
- PayPal
- Mobile Money checkout
- Credit card payment forms
- In-app checkout
- Subscription billing
- Payment processing
- Automatic payment verification

The system may record internal payment-related notes or references if needed for administration, but it must not process payments.

## 4. ACCESS-CODE ARCHITECTURE

Build a secure, database-backed access-control system.

Suggested entities:

- User
- Role
- Permission
- AccessCode
- AccessGrant
- AccessRedemption
- ActivityLog
- AdminSettings

An access code should support:

- Unique identifier
- Secure random secret
- Status
- Duration
- Activation timestamp
- Expiry timestamp
- Redemption limit
- Redemption count
- Assigned user, where applicable
- Created by
- Created timestamp
- Revoked timestamp
- Revocation reason
- Internal notes

Use secure random code generation.

Never use predictable sequential codes.

Store sensitive code values securely where the existing architecture supports it. Do not expose code secrets in ordinary API responses or logs.

## 5. ACCESS DURATION

The Super Admin must be able to create access codes with:

- 1 day
- 3 days
- 7 days
- 14 days
- 30 days
- 60 days
- 90 days
- 180 days
- 365 days
- Custom duration

The Super Admin should be able to define:

- Duration in days
- Duration in hours, if supported
- Activation behavior
- Redemption limit
- Optional user assignment

Define and implement a clear activation policy.

Recommended default:

**The access period begins when the code is successfully redeemed.**

If the Super Admin explicitly assigns an access period to a specific user, the system may use that assigned period instead.

The selected policy must be visible to the administrator and consistently enforced.

## 6. ACCESS VALIDATION

When a user redeems a code, validate it on the backend.

Check:

- Code exists
- Code is valid
- Code has not been revoked
- Code has not expired
- Redemption limit has not been exceeded
- User is eligible
- Code is not already assigned to another user
- Redemption request is authorized
- Request is not a duplicate
- Code has not exceeded redemption attempts

Use a database transaction or equivalent atomic operation to prevent race conditions.

On successful redemption:

1. Create an access grant.
2. Record the activation timestamp.
3. Calculate the expiry timestamp.
4. Associate the grant with the user.
5. Record the redemption event.
6. Update the code's redemption count.
7. Record the relevant audit information.
8. Return the user's updated access status.

Never allow the frontend to determine whether a code is valid.

## 7. USER ROLES AND PERMISSIONS

Implement server-side role-based access control.

Required roles:

### SUPER_ADMIN

Can:

- Manage users
- Generate access codes
- Revoke access codes
- Extend access
- Suspend users
- Activate users
- View access history
- Manage permissions
- View activity logs
- View system analytics
- Manage platform settings
- Access the prediction system

### USER

Can:

- Access permitted football predictions
- View match analysis
- View available football statistics
- Redeem eligible access codes
- View personal access status
- View personal prediction history, if supported
- Manage personal profile
- Manage personal settings

Do not rely on frontend route guards alone.

Enforce authorization on every protected backend endpoint.

Users must never be able to modify:

- Their own role
- Their access expiry
- Their access status
- Their permissions
- Another user's access
- Super Admin settings

## 8. SUPER ADMIN DASHBOARD

Design and implement a premium Super Admin command center.

The interface should be modern, futuristic, professional, and easy to operate.

Primary navigation:

- Overview
- Users
- Access Codes
- Access Grants
- Football Predictions
- Football Data
- Analytics
- Activity Logs
- Profile
- Settings

Do not display unsupported features as functional.

### Dashboard statistics

Display real database values for:

- Total users
- Active users
- Suspended users
- Users with active access
- Expired access grants
- Unused access codes
- Redeemed access codes
- Revoked access codes
- Predictions generated, if available

Use elegant stat cards, charts, and visual indicators.

Do not fabricate metrics.

## 9. ACCESS-CODE MANAGEMENT UI

Create a dedicated access-code management interface.

Features:

- Generate code
- Search codes
- Filter codes
- Sort codes
- View code details
- Copy code
- Revoke code
- View redemption history
- Assign code to a user
- Extend access
- View expiry
- View audit history

Suggested table columns:

- Code identifier
- Status
- Duration
- Redemption limit
- Redemption count
- Assigned user
- Created date
- Activation date
- Expiry date
- Created by
- Actions

Build a professional generation modal with:

- Duration selection
- Custom duration
- Redemption limit
- Optional user assignment
- Internal notes
- Activation policy
- Generate button

After successful generation, display the code securely.

The code must not be shown as successfully generated until the backend confirms creation.

## 10. USER MANAGEMENT

Create a Super Admin user-management interface.

Features:

- View users
- Search users
- Filter users
- View user profile
- View access grants
- Suspend user
- Activate user
- Revoke access
- Extend access
- Assign access code
- View prediction usage
- View login history where supported
- View activity history

User profile should show:

- Name
- Email
- Account status
- Role
- Registration date
- Last login
- Current access status
- Current access expiry
- Access history
- Prediction usage, if available

Use confirmation dialogs for destructive actions.

## 11. SUPER ADMIN PROFILE AND SETTINGS

Create a dedicated Super Admin profile page.

### Profile

- Full name
- Email
- Phone, if supported
- Profile photo
- Role
- Account creation date
- Last login
- Edit profile
- Change password

### Security

- Change password
- Active sessions, if supported
- Login history, if supported
- Two-factor authentication, if supported by the existing architecture

### Settings

- Default access duration
- Default redemption limit
- Access activation policy
- Access expiration behavior
- User management settings
- Notification preferences
- Platform appearance
- Timezone
- Football data preferences, where applicable

Do not create settings that have no corresponding backend behavior.

## 12. USER EXPERIENCE

Create a premium football prediction dashboard.

Main navigation:

- Overview
- Football Matches
- AI Predictions
- Match Analysis
- Prediction History, if supported
- Access
- Profile
- Settings

At the top of the dashboard show:

- User name
- Current access status
- Access expiry
- Remaining access time
- Current plan or access duration, if supported

### Access status

Use clear statuses:

- ACTIVE
- EXPIRED
- REVOKED
- SUSPENDED
- PENDING

When access expires, lock protected prediction functionality and show:

"Your football intelligence access has expired."

Provide:

"Enter Access Code"

Do not automatically extend access.

## 13. FOOTBALL-ONLY LANDING PAGE

Create a visually exceptional landing page for the football AI platform.

The design should feel like:

- A futuristic football intelligence terminal
- A premium AI analytics product
- A modern sports technology platform

### Visual direction

Use:

- Dark premium background
- Football-inspired visual elements
- Subtle gradients
- Glassmorphism where appropriate
- Refined glowing borders
- Elegant data visualizations
- Modern typography
- Smooth transitions
- Football iconography
- Animated match cards
- AI analysis visualizations

Use the existing brand identity if available.

Do not use unrelated sports icons.

### Hero section

Suggested headline:

"THE FUTURE OF AI FOOTBALL INTELLIGENCE"

Supporting text:

"Analyze football matches through advanced data, team performance, player information, and AI-powered probability analysis."

Primary CTA: "Explore AI Predictions"

Secondary CTA: "Enter Access Code"

Do not claim guaranteed prediction accuracy.

### Football animations

Use tasteful animations such as:

- Slowly rotating football
- Football moving along a glowing trajectory
- Animated football pitch lines
- Floating match cards
- Subtle stadium-light effects
- Moving data particles
- AI scanning effects
- Animated probability visualizations
- Smooth page transitions

Animations must not harm usability or performance.

Support reduced-motion preferences.

## 14. LANDING PAGE SECTIONS

Build the following sections.

### Hero

- Futuristic football visual
- AI analysis interface
- Animated match cards
- Primary and secondary CTAs

### Football Intelligence

Explain the platform's actual capabilities.

Possible features:

- Team form analysis
- Head-to-head analysis
- Player performance
- Injuries and suspensions
- Team news
- Home and away performance
- League statistics
- Tactical information
- Match conditions
- Live match statistics, if supported

Only display features supported by the actual data and prediction system.

### How It Works

Four steps:

1. Football data
2. AI analysis
3. Probability calculation
4. Prediction output

### Prediction Preview

Create a polished demonstration card.

Clearly label it as DEMO DATA if it is not connected to live data.

Example:

```
ARSENAL vs CHELSEA
AI PREDICTION
Home Win — 55%
Draw — 25%
Away Win — 20%
```

The values above are illustrative only.

### Access Process

Explain:

1. Purchase access externally.
2. Receive a unique access code.
3. Enter the code.
4. Start using the football AI platform.

Do not add payment forms.

### Final CTA

"ENTER THE FOOTBALL INTELLIGENCE TERMINAL"

Button: "Get Access"

### Footer

Include:

- About
- Contact
- Terms
- Privacy
- Disclaimer
- Access Code
- Login

## 15. EXISTING TRAINED AI MODEL INTEGRATION

The existing trained model is the source of prediction intelligence.

Before changing the prediction UI:

1. Locate the model.
2. Identify its inference interface.
3. Identify required input features.
4. Identify its output format.
5. Identify how model predictions are currently generated.
6. Identify how prediction confidence or probabilities are calculated.
7. Identify whether the model supports pre-match and live predictions.
8. Identify the current football data pipeline.

Preserve the existing model.

Do not silently modify its weights, training process, features, or inference behavior.

Create a clean integration layer if required.

The UI must consume actual prediction results.

Do not generate fake AI outputs.

Do not label a result as live unless the underlying data is live.

Do not display a confidence score unless the system defines and supports it.

## 16. PREDICTION INTERFACE

Create a premium football prediction interface.

Display, where supported:

- Home team
- Away team
- Competition
- Kickoff time
- Match status
- Team logos
- Recent form
- Head-to-head data
- Relevant team statistics
- Player availability
- AI prediction
- Outcome probabilities
- Key contributing factors
- Data timestamp
- Prediction generation time

The system may display supported football markets such as:

- Home win
- Draw
- Away win
- Over/under goals
- Both teams to score
- Other existing model outputs

Only display markets that the trained model actually supports.

Clearly distinguish probability from certainty.

Example:

"Most likely outcome: Home Win"

"Model probability: 58%"

Do not present a probability as a guarantee.

## 17. FOOTBALL MATCH EXPLORER

Build a football match browsing interface.

Features may include:

- Upcoming matches
- Live matches, if supported
- Completed matches
- Search by team
- Filter by competition
- Filter by date
- Match details
- Prediction availability
- Match status

Use actual football data from the existing backend.

Do not invent fixtures, teams, scores, or statistics.

If the application does not yet have a football-data provider, identify the missing integration and document it rather than pretending the feature is operational.

## 18. ACCESS-CODE REDEMPTION PAGE

Create a polished page for users to activate access.

Heading: "ACTIVATE YOUR FOOTBALL AI ACCESS"

Supporting text: "Enter the unique access code provided to you after your external purchase."

Input: ACCESS CODE

Button: "ACTIVATE ACCESS"

States:

- Empty
- Loading
- Invalid code
- Expired code
- Revoked code
- Redemption limit reached
- Successful activation
- Network error

After success, display:

"ACCESS ACTIVATED"

- Access status
- Activation date
- Expiry date
- Duration
- Button to enter the football platform

Use clear, secure error messages.

## 19. SECURITY REQUIREMENTS

Implement security according to the existing backend architecture.

Required protections:

- Secure authentication
- Server-side role enforcement
- Secure access-code generation
- Rate limiting for code redemption
- Brute-force protection
- Input validation
- Authorization on protected APIs
- Database transaction safety
- Secure session handling
- Audit logging
- Protection against expiry manipulation
- Protection against role manipulation
- Protection against unauthorized access to prediction endpoints

A user with expired or revoked access must not be able to call protected prediction APIs successfully.

Do not rely on hiding UI components as an access-control mechanism.

Do not expose access-code secrets in logs.

## 20. DESIGN SYSTEM

Create a consistent, reusable design system.

Use the project's existing UI framework where appropriate.

Recommended components:

- Button
- Card
- Modal
- Badge
- Table
- Input
- Select
- Dialog
- StatCard
- PredictionCard
- FootballMatchCard
- AccessCodeCard
- StatusIndicator
- Countdown
- LoadingState
- EmptyState
- ErrorState
- ConfirmationDialog
- Sidebar
- TopNavigation

Design principles:

- Consistent spacing
- Strong typography
- Responsive layouts
- Accessible contrast
- Clear focus states
- Consistent button hierarchy
- Professional data tables
- Smooth but restrained animations

Do not introduce unnecessary dependencies.

## 21. RESPONSIVE EXPERIENCE

Support:

- Desktop
- Laptop
- Tablet
- Mobile

On mobile:

- Use a responsive navigation drawer.
- Ensure prediction cards remain readable.
- Make access-code entry simple.
- Use responsive tables or cards.
- Ensure buttons have appropriate touch targets.
- Preserve the visual quality of the landing page.

Optimize for fast loading.

Respect reduced-motion preferences.

## 22. TESTING REQUIREMENTS

Before declaring the work complete, test:

### Authentication

- Login
- Logout
- Session persistence
- Unauthorized access
- Role enforcement

### Access codes

- Generate code
- Redeem valid code
- Reject invalid code
- Reject revoked code
- Reject expired code
- Enforce redemption limit
- Prevent duplicate redemption
- Activate access
- Calculate expiry correctly
- Extend access
- Revoke access

### Permissions

- User cannot access admin routes
- User cannot modify their role
- User cannot modify expiry
- User cannot bypass expired access
- User cannot call protected prediction APIs without access
- Super Admin can manage users and access codes

### Prediction integration

- Existing model remains functional
- Existing prediction API remains functional
- Actual model outputs render correctly
- Prediction errors are handled
- Missing data is handled

### UI

- Landing page
- Authentication pages
- User dashboard
- Admin dashboard
- Profile
- Settings
- Access-code redemption
- Mobile layout
- Loading states
- Error states
- Empty states

Run the project's existing linting, type checks, and test suite.

Fix errors before finishing.

## 23. IMPLEMENTATION WORKFLOW

Follow this order:

**Phase 1 — Audit.** Understand the existing project and trained AI model.

**Phase 2 — Architecture.** Design the access-code and permission system.

**Phase 3 — Database.** Implement only the required schema changes.

**Phase 4 — Backend.** Implement secure access validation and protected APIs.

**Phase 5 — Design system.** Create the reusable visual foundation.

**Phase 6 — Landing page.** Build the futuristic football landing page.

**Phase 7 — Authentication.** Improve authentication UI and protected routing.

**Phase 8 — User dashboard.** Build the football prediction experience.

**Phase 9 — Access redemption.** Build code activation and expiry UI.

**Phase 10 — Super Admin.** Build user, access-code, profile, and settings interfaces.

**Phase 11 — Integration.** Connect the existing trained model and actual football data.

**Phase 12 — Security.** Test backend authorization and access restrictions.

**Phase 13 — Responsive design.** Optimize every page for mobile and desktop.

**Phase 14 — Testing.** Run tests, type checks, and linting.

**Phase 15 — Final polish.** Improve visual quality, transitions, accessibility, and performance.

## 24. CLAUDE CODE EXECUTION RULES

When working on this project:

1. Inspect before implementing.
2. Reuse existing infrastructure.
3. Preserve the trained AI model.
4. Do not invent APIs.
5. Do not invent football data.
6. Do not fabricate analytics.
7. Do not create payment functionality.
8. Do not expose secrets.
9. Do not trust frontend access status.
10. Do not make unsupported accuracy claims.
11. Do not rewrite the entire application unnecessarily.
12. Keep changes modular.
13. Explain important architectural decisions.
14. Test every major feature.
15. Verify that existing prediction functionality still works.

If a required backend feature is missing, implement it using the existing architecture where practical.

If a critical dependency or data source is missing, identify it clearly and document the integration required.

## 25. FINAL ACCEPTANCE CRITERIA

The finished platform must include:

- Football-only product experience
- Futuristic animated landing page
- Football-specific visual design
- Existing trained AI model preserved
- Actual prediction integration
- Authentication
- User dashboard
- Access-code redemption
- Time-limited access
- Automatic access expiration
- Access revocation
- Super Admin dashboard
- User management
- Access-code management
- Super Admin profile
- Super Admin settings
- Server-side permissions
- Activity logging
- Responsive design
- Loading and error states
- Accessibility
- Security protections
- No integrated payment gateway
- No guaranteed prediction claims

Begin by auditing the repository and producing the implementation plan. Do not start by replacing existing files or retraining the model.
