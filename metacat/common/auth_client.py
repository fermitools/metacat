from metacat.util import to_str, to_bytes
from metacat.common import SignedToken, TokenLib
import time, requests, json

import urllib3      # disable "Unverified HTTPS request is being made..." warning
import os

urllib3.disable_warnings()
del urllib3

class AuthenticationError(Exception):
    def __init__(self, message):
        self.Message = message

    def __str__(self):
        msg = self.Message or "Authentication error"
        return msg

class TokenAuthClientMixin(object):
    
    def __init__(self, service_url, auth_url, token=None, token_file=None, token_library=None):
        self.ServiceURL = service_url
        self.AuthURL = auth_url or service_url + "/auth"
        #print("TokenAuthClientMixin: AuthURL:", self.AuthURL)
        self.TokenLib = TokenLib(token_library)
        if isinstance(token, (str, bytes)):
            token = SignedToken.decode(token)

        self.TokenFile = token_file
        self.Token = token 
        if self.Token is None: self.Token = self.token()      # load from file/lib if needed
        
    def auth_headers(self):
        if self.Token:
            return {"X-Authentication-Token":self.Token.encode()}

    def token(self):
        if self.Token is None or self.Token.expiration <= time.time():
            if self.TokenFile:
                token = open(self.TokenFile, "rb").read()
                self.Token = SignedToken.decode(token)
            else:
                self.Token = self.TokenLib.get(self.ServiceURL)
        return self.Token
        
    def tokens_saved(self):
        return self.TokenLib is not None and self.TokenLib.exists()

    def token_update(self):
        if self.tokens_saved():
            try:
                libtime = os.stat(self.TokenLib.Location).st_mtime
                if  libtime > self.TokenLib.LoadTime:
                     self.TokenLib.Tokens, _  = self.TokenLib.load_library([self.TokenLib.Location])
                     self.Token = self.TokenLib.get(self.ServiceURL)
            except:
                raise


    def login_digest(self, username, password, save_token=False):
        """Performs password-based authentication and stores the authentication token locally.
        
        Parameters
        ----------
        username : str
        password : str
            Password is not sent over the network. It is hashed and then used for digest authentication (:rfc:`2617`).

        Returns
        -------
        str
            username of the authenticated user (same as ``usernme`` argument)
        numeric
            token expiration timestamp
            
        """
        from requests.auth import HTTPDigestAuth
        auth_url = self.AuthURL
        url = "%s/%s?method=digest" % (auth_url, "auth")
        #print("login_digest: url:", url)
        response = requests.get(url, verify=False, auth=HTTPDigestAuth(username, password))
        if response.status_code != 200:
            #print(response, response.text)
            raise AuthenticationError(response.text)
        #print(response)
        #print(response.headers)
        self.Token = token = SignedToken.decode(response.headers["X-Authentication-Token"])
        #print("token:", token.Payload)
        if self.TokenLib is not None:
            self.TokenLib[self.ServerURL] = token
        return token.subject, token.expiration

    def login_ldap(self, username, password):
        """Performs password-based authentication and stores the authentication token locally using LDAP.
        
        Parameters
        ----------
        username : str
        password : str
            Password 

        Returns
        -------
        str
            username of the authenticated user (same as ``usernme`` argument)
        numeric
            token expiration timestamp
            
        """
        auth_url = self.AuthURL
        url = "%s/%s?method=ldap" % (auth_url, "auth")        
        data = b"%s:%s" % (to_bytes(username), to_bytes(password))
        response = requests.post(url, verify=False, data = data)
        if response.status_code != 200:
            raise AuthenticationError(response.text)
	
        self.Token = token = SignedToken.decode(response.headers["X-Authentication-Token"])
        if self.TokenLib is not None:
            self.TokenLib[self.ServerURL] = token
        return token.subject, token.expiration

    def login_password(self, username, password):
        """Combines LDAP and :rfc:`2617` digest authentication by calling `login_ldap` first and then, if it fails, `ldap_digest` methods

        Parameters
        ----------
        username : str
        password : str
            Password 

        Returns
        -------
        str
            username of the authenticated user (same as ``usernme`` argument)
        numeric
            token expiration timestamp

        """
        user = None
        try:
            user, exp = self.login_ldap(username, password)
        except AuthenticationError:
            #print("LDAP authentication failed, trying digest")
            user, exp = self.login_digest(username, password)
        except Exception as e:
            #print(e)
            raise
        else:
            pass
            #print("Digest authentication succeeded:", user, exp)
        #print("logn_password:", user, exp)
        return user, exp
            
    def login_token(self, username, encoded_token):
        """Authenticate using a JWT or a SciToken.

        Parameters
        ----------
        username : str
        encoded_token : str or bytes

        Returns
        -------
        str
            username of the authenticated user (same as ``usernme`` argument)
        numeric
            authentication expiration timestamp
        """
        url = f"{self.AuthURL}/auth?method=token&username={username}"    
        headers = {"Authorization": "bearer " + to_str(encoded_token)}
        #print("HTTPClient.post_json: headers:", headers)
        #print("url:", url)
        response = requests.post(url, verify=False, headers=headers)
        if response.status_code != 200:
            raise AuthenticationError(response.text)
	
        self.Token = token = SignedToken.decode(response.headers["X-Authentication-Token"])
        if self.TokenLib is not None:
            self.TokenLib[self.ServerURL] = token
        return token.subject, token.expiration
            
    def my_x509_dn(self, cert, key=None):
        auth_url = self.AuthURL
        url = f"{auth_url}/mydn"    
        cert_arg = (cert, cert) if key is None else (cert, key)
        response = requests.get(url, verify=False, cert=cert_arg)
        if response.status_code != 200:
            raise AuthenticationError(response.text)
        data = json.loads(response.text)
        return data

    def login_x509(self, username, cert, key=None):
        """Performs X.509 authentication and stores the authentication token locally.
        
        Parameters
        ----------
        username : str
        cert : str
            Path to the file with the X.509 certificate or the certificate and private key
        key : str
            Path to the file with the X.509 private key
         

        Returns
        -------
        str
            username of the authenticated user (same as ``usernme`` argument)
        numeric
            token expiration timestamp
            
        """
        auth_url = self.AuthURL
        url = f"{auth_url}/auth?method=x509&username={username}"    
        cert_arg = (cert, cert) if key is None else (cert, key)
        response = requests.get(url, verify=False, cert=cert_arg)
        if response.status_code != 200:
            raise AuthenticationError(response.text)
        self.Token = token = SignedToken.decode(response.headers["X-Authentication-Token"])
        if self.TokenLib is not None:
            self.TokenLib[self.ServerURL] = token
        return token.subject, token.expiration

    def auth_info(self):
        """Returns information about current authentication token.
        
        Returns
        -------
        str
            username of the authenticated user
        numeric
            token expiration timestamp
            
        """
        server_url = self.ServerURL
        token = self.Token
        if not token:
            if self.TokenLib:
                token = self.TokenLib.get(server_url)
        if not token:
            raise AuthenticationError("No token found")
        url = self.AuthURL + "/verify"
        response = requests.get(url, headers={
                "X-Authentication-Token":token.encode()
        })
        #print("web_api.auth_info:", response.status_code, response.text)
        if response.status_code/100 == 2:
            return token.subject, token.expiration
        else:
            raise AuthenticationError(response.text)
