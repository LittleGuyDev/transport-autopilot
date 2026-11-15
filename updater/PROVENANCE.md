# Automatic updater design and provenance

This updater was written for Little Guy Dev's Transport Autopilot and Tourism
Dashboard. Implementation agents received the requirements and our existing
installer APIs, without another author's installer or launcher source. No
third-party installer code is imported or distributed. A pre-launch launcher is
a general design pattern; independent implementation does not make that idea
exclusive to this project.

The signature verifier follows RSA-3072 with SHA-256 and the strict encoded-block
comparison in [RFC 8017 sections 8.2.2 and 9.2](https://www.rfc-editor.org/rfc/rfc8017).
Runtime verification uses Python's standard library. OpenSSL is a publisher-side
signing and test dependency, not a player dependency. The publisher's private
key stays outside the repository and all release packages. First installation
is the trust boundary; future downloads cannot replace the pinned public key.

The updater checks only explicitly enrolled copies of our two products. It
authenticates complete release contents, copies a verified private snapshot,
uses isolated Python startup, and checks both products before changing either.
It rejects a lower accepted release sequence or conflicting bytes at the same
sequence. This is rollback protection against the user's accepted history, not
an online guarantee that Steam has delivered the newest available release.

One shared coordinator lock orders updates, manual installations, removal and
game launch. Each installer retains its own lock, exact ownership checks,
compatibility checks, verified backups, transactional recovery and platform
integrity/signature updates. A failed update stops the launch. Removing a mod
also removes its automatic-update enrollment.

The no-change path validates the signed package, current game identity, owned
scripts, embedded integrity and registration data without repeatedly hashing
the entire multi-gigabyte archive. Actual changes use the full installer and
its source-change checks. Native Steam launch-option interception is tested
using a disposable dummy installation, with original settings restored.

Initial installation authenticates the exact recorded size and hash of the
signed installer-only `steam_setup.py` helper (source `updater/steam.py`) before
loading it and passing it to source setup registration. The signed package has
six source helpers; the five pinned cached runtime files remain unchanged and
compatible. Direct API registration retains the legacy manual setup fallback;
that fallback is not the normal customer installer flow.

Steam setup validates the selected account and app 940230 before changing only
that app's Launch Options field. A private backup and journal support recovery.
Plain game flags survive; unknown wrappers and ambiguous accounts stop setup
without replacing those settings. Steam must close normally; it is never killed.
Both products share one hook. Last removal restores the prior field only while
it is still the managed value, preserving user edits and any needed runtime.

The installed coordinator uses protocol 1 and preserves its initial trust
runtime. A future incompatible updater protocol requires deliberate re-setup.
No background service, administrator task or independent network downloader is
installed. Steam supplies downloads; the updater acts before game startup.
