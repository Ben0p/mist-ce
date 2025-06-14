import { PolymerElement, html } from '@polymer/polymer/polymer-element.js';

class MistPortal extends PolymerElement {
  static get template() {
    return html`
      <style>
        :host {
          display: block;
          height: 100%;
          background-color: var(--base-background-color);
        }
      </style>
      <slot></slot>
    `;
  }

  static get is() {
    return 'mist-portal';
  }
}

window.customElements.define(MistPortal.is, MistPortal);