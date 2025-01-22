// Check login status when page loads
document.addEventListener('DOMContentLoaded', function() {
    checkLoginStatus();
});

function checkLoginStatus() {
    const isLoggedIn = sessionStorage.getItem('isLoggedIn');
    if (isLoggedIn) {
        showChat();
    } else {
        showLogin();
    }
}

function showChat() {
    document.getElementById('loginForm').style.display = 'none';
    document.getElementById('chatInterface').style.display = 'block';
}

function showLogin() {
    document.getElementById('loginForm').style.display = 'block';
    document.getElementById('chatInterface').style.display = 'none';
}

function checkLogin(event) {
    event.preventDefault();
    
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value.trim();
    
    // Simple hash of the credentials (not secure but better than plaintext in source)
    const validUsername = btoa('demo');
    const validPassword = btoa('demo99');
    
    if (btoa(username) === validUsername && btoa(password) === validPassword) {
        sessionStorage.setItem('isLoggedIn', 'true');
        showChat();
    } else {
        alert('Invalid credentials. Please try again.');
        document.getElementById('password').value = '';
    }
    return false;
}

function logout() {
    sessionStorage.removeItem('isLoggedIn');
    showLogin();
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
}

// Prevent access to chat interface through console
setInterval(checkLoginStatus, 1000);