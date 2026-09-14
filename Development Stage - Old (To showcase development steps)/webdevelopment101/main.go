import (
	"context"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"golang.org/x/oauth2"
	"github.com/coreos/go-oidc"
	"github.com/golang-jwt/jwt/v4"
)

type ClaimsPage struct {
	AccessToken string
	Claims      jwt.MapClaims
}

var (
	clientID     = "4os614jcrtrq9kkrqel52692e7"
	clientSecret = "1emj5j1i63ckutgiq5cj4niplrun22gbjgqanmfe8uirsel5ccmv"
	redirectURL  = "https://d84l1y8p4kdic.cloudfront.net"
	issuerURL    = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_asEJWdOMO"
	provider     *oidc.Provider
	oauth2Config oauth2.Config
)

func init() {
	var err error
	// Initialize OIDC provider
	provider, err = oidc.NewProvider(context.Background(), issuerURL)
	if err != nil {
		log.Fatalf("Failed to create OIDC provider: %v", err)
	}

	// Set up OAuth2 config
	oauth2Config = oauth2.Config{
		ClientID:     clientID,
		ClientSecret: clientSecret,
		RedirectURL:  redirectURL,
		Endpoint:     provider.Endpoint(),
		Scopes:       []string{oidc.ScopeOpenID, "email", "openid", "phone"},
	}
}

func main() {
	http.HandleFunc("/", handleHome)
	http.HandleFunc("/login", handleLogin)
	http.HandleFunc("/logout", handleLogout)
	http.HandleFunc("/callback", handleCallback)

	fmt.Println("Server is running on http://localhost:8080")
	log.Fatal(http.ListenAndServe(":8080", nil))
}

func handleHome(w http.ResponseWriter, r *http.Request) {
	html := `
        <html>
        <body>
            <h1>Welcome to Cognito OIDC Go App</h1>
            <a href="/login">Login with Cognito</a>
        </body>
        </html>`
	fmt.Fprint(w, html)
}

func handleLogin(writer http.ResponseWriter, request *http.Request) {
	state := "state" // Replace with a secure random string in production
	url := oauth2Config.AuthCodeURL(state, oauth2.AccessTypeOffline)
	http.Redirect(writer, request, url, http.StatusFound)
}

func handleCallback(writer http.ResponseWriter, request *http.Request) {
	ctx := context.Background()
	code := request.URL.Query().Get("code")

	// Exchange the authorization code for a token
	rawToken, err := oauth2Config.Exchange(ctx, code)
	if err != nil {
		http.Error(writer, "Failed to exchange token: "+err.Error(), http.StatusInternalServerError)
		return
	}
	tokenString := rawToken.AccessToken

	// Parse the token (do signature verification for your use case in production)
	token, _, err := new(jwt.Parser).ParseUnverified(tokenString, jwt.MapClaims{})
	if err != nil {
		fmt.Printf("Error parsing token: %v\n", err)
		return
	}

	// Check if the token is valid and extract claims
	claims, ok := token.Claims.(jwt.MapClaims)
	if !ok {
		http.Error(writer, "Invalid claims", http.StatusBadRequest)
		return
	}

	// Prepare data for rendering the template
	pageData := ClaimsPage{
		AccessToken: tokenString,
		Claims:      claims,
	}

	// Define the HTML template
	tmpl := `
    <html>
        <body>
            <h1>User Information</h1>
            <h1>JWT Claims</h1>
            <p><strong>Access Token:</strong> {{.AccessToken}}</p>
            <ul>
                {{range $key, $value := .Claims}}
                    <li><strong>{{$key}}:</strong> {{$value}}</li>
                {{end}}
            </ul>
            <a href="/logout">Logout</a>
        </body>
    </html>`

	// Parse and execute the template
	t := template.Must(template.New("claims").Parse(tmpl))
	t.Execute(writer, pageData)
}

func handleLogout(writer http.ResponseWriter, request *http.Request) {
	// Here, you would clear the session or cookie if stored.
	http.Redirect(writer, request, "/", http.StatusFound)
}