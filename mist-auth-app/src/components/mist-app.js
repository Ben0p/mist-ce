import { PolymerElement, html } from '@polymer/polymer/polymer-element.js';
import './mist-portal.js';

class MistApp extends PolymerElement {
  static get template() {
    return html`
      <style>
        :host {
          display: block;
          height: 100%;
        }
      </style>
      <mist-portal></mist-portal>
    `;
  }

  static get is() {
    return 'mist-app';
  }
}

window.customElements.define(MistApp.is, MistApp);