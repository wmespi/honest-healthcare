# Sharing access with a tester

*For the person running the app, sending the link to someone who isn't a
developer — a family member, a tester. Two steps: invite them to your
private network, then they open a link.*

There's no public website. The app runs on your machine and is reached over
[Tailscale](https://tailscale.com), a private network — only people you
explicitly invite can see it, and it never touches the public internet. This
is the tester's side; your side (setting up serving) is
[`scripts/tailscale-up.sh`](../scripts/tailscale-up.sh).

---

## 1. Invite them

1. Open the [Tailscale admin console](https://login.tailscale.com/admin/users) → **Users** → **Invite external user**.
2. Enter their email address and send the invite.
3. They'll get an email with a link to accept it.

They only need access to *your* tailnet — nothing else on it is exposed by
this; the app is the one thing being served.

## 2. They install Tailscale and sign in

1. On their phone or laptop, install the Tailscale app:
   [iOS](https://apps.apple.com/app/tailscale/id1470499037) ·
   [Android](https://play.google.com/store/apps/details?id=com.tailscale.ipn) ·
   [Mac / Windows / Linux](https://tailscale.com/download)
2. Open the app and sign in — the invite email's link, or just signing in
   with the same email you invited, will pull them onto your tailnet.
3. Once signed in, leave the app running in the background (it doesn't need
   to stay open, just installed and signed in) — that's what makes the link
   below resolve.

## 3. Send them the link

`http://<your-machine>.<your-tailnet>.ts.net:5173`

Find your exact link by running `tailscale status` and reading `Self`'s
`DNSName`, or ask whoever set up serving — it's printed at the end of
`scripts/tailscale-up.sh`.

**What they'll see:** the browser will say "Not Secure" — that's expected,
not a warning to worry about. It means plain HTTP inside your private
tailnet, not a public site; nothing is exposed outside the people you
invited in step 1.

**If the link doesn't load:**
- Confirm they're signed into the Tailscale app on the device they're using.
- Your machine has to be on and the app running — this isn't hosted
  anywhere else. Check `docker compose ps` on your side.
- Tailscale's admin console (**Machines**) shows whether their device
  actually joined the tailnet.

## Revoking access

Admin console → **Users** → remove them, or **Machines** → remove their
device. Either stops their access immediately.
