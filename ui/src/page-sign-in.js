import { PolymerElement, html } from '@polymer/polymer/polymer-element.js';

class PageSignIn extends PolymerElement {
  static get template() {
    return html`
      <style>
        :host {
          display: block;
          padding: 16px;
          font-family: sans-serif;
        }
      </style>
      <h1>hello world</h1>
    `;
  }
  
  static get is() { return 'page-sign-in'; }
}

window.customElements.define(PageSignIn.is, PageSignIn);