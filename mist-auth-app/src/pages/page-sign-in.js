class PageSignIn extends PolymerElement {
  static get template() {
    return html`
      <style>
        :host {
          display: block;
          padding: 16px;
          font-family: sans-serif;
        }
        .sign-in-container {
          max-width: 400px;
          margin: auto;
          text-align: center;
        }
        h1 {
          color: #333;
        }
        input {
          width: 100%;
          padding: 10px;
          margin: 10px 0;
          border: 1px solid #ccc;
          border-radius: 4px;
        }
        button {
          padding: 10px 15px;
          background-color: #007bff;
          color: white;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }
        button:hover {
          background-color: #0056b3;
        }
      </style>
      <div class="sign-in-container">
        <h1>Sign In</h1>
        <input type="text" placeholder="Username" required>
        <input type="password" placeholder="Password" required>
        <button @click="${this._handleSignIn}">Sign In</button>
      </div>
    `;
  }
  
  static get is() { return 'page-sign-in'; }

  _handleSignIn() {
    // Future authentication logic will go here
  }
}

window.customElements.define(PageSignIn.is, PageSignIn);