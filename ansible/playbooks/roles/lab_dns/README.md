# Lab DNS

Opt-in test-only host resolver; see [inputs and procedure](../../../../docs/lab-dns.md).
`lab_dns_state` is `unmanaged` by default; `present` adopts/configures the resolver
and `absent` removes it after a verified host DNS handoff. The role preserves the
provider resolver link and other drop-ins. Namespace, records, LAN listener and
upstreams belong in private host inputs.
