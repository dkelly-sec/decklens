// DeckLens login page. Messages are set with textContent only (see app.js).
"use strict";

async function logIn(event) {
  event.preventDefault();
  const message = document.getElementById("login-message");
  const button = document.querySelector("#login-form button");
  message.className = "result muted";
  message.textContent = "Logging in…";
  button.disabled = true;
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("username").value,
        password: document.getElementById("password").value,
      }),
    });
    if (response.ok) {
      window.location.replace("/");
      return;
    }
    let detail = "Couldn't log in";
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch { /* not JSON */ }
    if (response.status === 422) detail = "Check your username and password";
    message.className = "result error";
    message.textContent = detail;
    document.getElementById("password").value = "";
  } catch {
    message.className = "result error";
    message.textContent = "Couldn't reach the server";
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-form").addEventListener("submit", logIn);
  document.getElementById("username").focus();
});
