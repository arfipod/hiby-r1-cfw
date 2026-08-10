/* HiBy R1 maintenance build: small server, no tunnelling features. */
#define DROPBEAR_DEFPORT "2222"
#define DROPBEAR_X11FWD 0
#define DROPBEAR_SVR_LOCALTCPFWD 0
#define DROPBEAR_SVR_REMOTETCPFWD 0
#define DROPBEAR_SVR_LOCALSTREAMFWD 0
#define DROPBEAR_SVR_REMOTESTREAMFWD 0
#define DROPBEAR_SVR_AGENTFWD 0
#define DROPBEAR_SFTPSERVER 0

/* Keep both methods: the lab image has a password, keys are preferred. */
#define DROPBEAR_SVR_PASSWORD_AUTH 1
#define DROPBEAR_SVR_PUBKEY_AUTH 1
#define DROPBEAR_SVR_PUBKEY_OPTIONS 1

/* Limit unauthenticated resource use and slow trivial brute force attempts. */
#define MAX_UNAUTH_PER_IP 2
#define MAX_UNAUTH_CLIENTS 4
#define MAX_AUTH_TRIES 3
#define UNAUTH_CLOSE_DELAY 2

/* No legacy SHA-1, DSS, 3DES or CBC compatibility. */
#define DROPBEAR_RSA_SHA1 0
#define DROPBEAR_DSS 0
#define DROPBEAR_3DES 0
#define DROPBEAR_ENABLE_CBC_MODE 0

