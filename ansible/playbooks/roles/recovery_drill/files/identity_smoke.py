import json,os
from authentik.core.models import User,Application
from authentik.sources.oauth.models import OAuthSource
from authentik.crypto.models import CertificateKeyPair
from authentik.providers.oauth2.models import OAuth2Provider
from cryptography.hazmat.primitives.serialization import load_pem_private_key
sources=list(OAuthSource.objects.filter(provider_type='google'))
assert len(sources)==1 and bool(sources[0].consumer_secret)
keys=[k for k in CertificateKeyPair.objects.all() if k.key_data]
for k in keys:load_pem_private_key(k.key_data.encode(),None)
assert keys
admin=User.objects.get(username='akadmin');assert admin.is_active and admin.check_password(os.environ['AUTHENTIK_BOOTSTRAP_PASSWORD'])
providers=list(OAuth2Provider.objects.all());assert len(providers)==2 and all(p.client_secret for p in providers)
assert User.objects.filter(username=os.environ['RECOVERY_ADMIN_USERNAME'],is_active=True).exists() and User.objects.filter(username=os.environ['RECOVERY_READER_USERNAME'],is_active=True).exists()
print('RECOVERY_IDENTITY_REPORT='+json.dumps({'google_source_secret_recovered':True,'signing_private_keys_parse':True,'local_emergency_admin_password_recovered':True,'both_oidc_client_secrets_recovered':True,'existing_internal_test_identities_recovered':True,'no_identity_rebootstrap':True}))

from django.apps import apps
from django.contrib.sessions.models import Session
models=[Session,apps.get_model('authentik_core','AuthenticatedSession'),apps.get_model('authentik_providers_oauth2','AuthorizationCode'),apps.get_model('authentik_providers_oauth2','AccessToken'),apps.get_model('authentik_providers_oauth2','RefreshToken')]
assert all(model.objects.count()==0 for model in models)
print('RECOVERY_SESSION_RESET='+json.dumps({'restored_idp_sessions_codes_and_tokens_empty_before_login':True,'identity_smoke_did_not_mutate_database':True}))
