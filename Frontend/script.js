let loginMode = "guest";
let userEmail = null;
let chats = [];
let currentChat = null;
let globalDocuments = [];
let chatDocuments = {};

/* ---------------- PROFILE DROPDOWN ---------------- */

function toggleProfileDropdown(event) {
    if (event) event.stopPropagation();
    var box = document.getElementById("profileBox");
    if (!box) return;
    box.classList.toggle("open");
    var trigger = document.getElementById("profileTrigger");
    if (trigger) trigger.setAttribute("aria-expanded", box.classList.contains("open"));
    if (box.classList.contains("open")) {
        document.addEventListener("click", closeProfileDropdownOnClickOutside);
    } else {
        document.removeEventListener("click", closeProfileDropdownOnClickOutside);
    }
}

function closeProfileDropdownOnClickOutside(e) {
    var box = document.getElementById("profileBox");
    var trigger = document.getElementById("profileTrigger");
    if (box && trigger && !box.contains(e.target)) {
        closeProfileDropdown();
    }
}

function closeProfileDropdown() {
    var box = document.getElementById("profileBox");
    if (box) {
        box.classList.remove("open");
        document.removeEventListener("click", closeProfileDropdownOnClickOutside);
    }
    var trigger = document.getElementById("profileTrigger");
    if (trigger) trigger.setAttribute("aria-expanded", "false");
}

/* ---------------- LOGIN POPUP ---------------- */

function openLoginPopup() {
    document.getElementById("loginPopup").style.display = "flex";
}

function closeLoginPopup() {
    document.getElementById("loginPopup").style.display = "none";
}

function showPersonalLogin() {
    document.getElementById("loginForm").innerHTML = `
        <input type="email" id="personalEmail" placeholder="Personal Email">
        <button onclick="loginPersonal()">Login</button>
    `;
}

function showCompanyLogin() {
    document.getElementById("loginForm").innerHTML = `
        <input type="email" id="companyEmail" placeholder="name@company.com">
        <button onclick="loginCompany()">Login</button>
    `;
}

/* ================= LOAD USER DATA ================= */

async function loadUserData(email) {
    try {
        // Load chats
        const chatRes = await fetch(`http://localhost:8000/chats/${email}`);
        const chatData = await chatRes.json();

        chats = chatData.chats || [];
        renderChats();

        // Load global documents (only for personal mode) - backend returns only global (chat_id null)
        if (loginMode === "personal") {
            const docRes = await fetch(`http://localhost:8000/documents/${email}`);
            const docData = await docRes.json();
            const names = docData.documents || [];
            globalDocuments = names.map(function (name) { return { name: name, file: null }; });
            renderGlobalDocs();

            // Load chat documents for each chat so count/list show after re-login
            for (let i = 0; i < chats.length; i++) {
                const chatName = chats[i];
                try {
                    const chatDocRes = await fetch(`http://localhost:8000/documents/${email}/${encodeURIComponent(chatName)}`);
                    const chatDocData = await chatDocRes.json();
                    const chatNames = chatDocData.documents || [];
                    chatDocuments[chatName] = chatNames.map(function (name) { return { name: name, file: null }; });
                } catch (err) {
                    console.error("Error loading docs for chat " + chatName, err);
                    chatDocuments[chatName] = chatDocuments[chatName] || [];
                }
            }
        }

    } catch (error) {
        console.error("Error loading user data:", error);
    }
}


/* ================= PERSONAL LOGIN ================= */

function loginPersonal() {
    const email = document.getElementById("personalEmail").value;

    if (!email || !email.includes("@")) {
        alert("Enter valid email");
        return;
    }

    loginMode = "personal";
    userEmail = email;

    // Show profile
    document.getElementById("loginBtn").style.display = "none";
    document.getElementById("profileBox").style.display = "block";

    const nameEl = document.getElementById("profileName");
    nameEl.textContent = email;
    nameEl.title = email;

    // Show document section
    document.getElementById("documentSection").style.display = "block";

    closeLoginPopup();

    // 🔥 Load existing chats & documents
    loadUserData(email);
}


/* ================= COMPANY LOGIN ================= */

function loginCompany() {
    const email = document.getElementById("companyEmail").value;

    if (!email || !email.includes("@")) {
        alert("Enter valid company email");
        return;
    }

    loginMode = "company";
    userEmail = email;

    // Show profile
    document.getElementById("loginBtn").style.display = "none";
    document.getElementById("profileBox").style.display = "block";

    const nameEl = document.getElementById("profileName");
    nameEl.textContent = email;
    nameEl.title = email;

    // Hide document section for company
    document.getElementById("documentSection").style.display = "none";

    const panel = document.getElementById("chatDocsPanel");
    if (panel) panel.style.display = "none";

    closeLoginPopup();

    //  Load existing chats (company mode)
    loadUserData(email);
}

function logout() {
    closeProfileDropdown();
    loginMode = "guest";
    userEmail = null;

    document.getElementById("loginBtn").style.display = "block";
    document.getElementById("profileBox").style.display = "none";

    chats = [];
    globalDocuments = [];
    chatDocuments = {};
    currentChat = null;

    document.getElementById("chatList").innerHTML = "";
    document.getElementById("chatArea").innerHTML = "";
    document.getElementById("chatTitle").innerText = "Select or Create Chat";
    var panel = document.getElementById("chatDocsPanel");
    if (panel) panel.style.display = "none";
    document.getElementById("documentSection").style.display = "none";
    renderGlobalDocs();
}

/* ---------------- CHAT LOGIC ---------------- */

function createChat() {
    const chatName = "Chat " + (chats.length + 1);
    chats.push(chatName);
    chatDocuments[chatName] = [];
    renderChats();
}

function renderChats() {
    const chatList = document.getElementById("chatList");
    chatList.innerHTML = "";

    chats.forEach(chat => {
        const li = document.createElement("li");
        li.innerText = chat;
        li.onclick = () => selectChat(chat);
        chatList.appendChild(li);
    });
}

async function selectChat(chatName) {

    currentChat = chatName;
    document.getElementById("chatTitle").innerText = chatName;
    document.getElementById("chatArea").innerHTML = "";

    // Show chat documents panel and load docs when in personal mode
    var panel = document.getElementById("chatDocsPanel");
    if (loginMode === "personal" && panel) {
        panel.style.display = "block";
        // If we don't have this chat's docs yet (e.g. new chat), fetch from API
        if (!chatDocuments[chatName]) {
            try {
                const chatDocRes = await fetch(`http://localhost:8000/documents/${userEmail}/${encodeURIComponent(chatName)}`);
                const chatDocData = await chatDocRes.json();
                const chatNames = chatDocData.documents || [];
                chatDocuments[chatName] = chatNames.map(function (name) { return { name: name, file: null }; });
            } catch (err) {
                console.error("Error loading docs for chat", err);
                chatDocuments[chatName] = [];
            }
        }
        renderChatDocs();
    } else if (panel) {
        panel.style.display = "none";
    }

    try {
        const res = await fetch(
            `http://localhost:8000/messages/${userEmail}/${chatName}`
        );

        const data = await res.json();

        (data.messages || []).forEach(msg => {
            addMessage(msg.content, msg.role);
        });

    } catch (error) {
        console.error("Error loading messages:", error);
    }
}

/* ---------------- DOCUMENTS (PERSONAL MODE ONLY) ---------------- */

function uploadGlobal() {
    if (loginMode !== "personal") return;
    const file = document.getElementById("globalUpload").files[0];
    if (!file) return;
    globalDocuments.push({ name: file.name, file: file });
    renderGlobalDocs();
    document.getElementById("globalUpload").value = "";
}

async function uploadChatDoc() {
    if (loginMode !== "personal") return;
    if (!currentChat) {
        alert("Select a chat first");
        return;
    }
    const fileInput = document.getElementById("chatUpload");
    const file = fileInput.files[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
        alert("Only PDF is supported");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("email", userEmail || "guest");
    formData.append("chat", currentChat);

    try {
        const response = await fetch("http://localhost:8000/upload", {
            method: "POST",
            body: formData
        });
        const data = await response.json();
        if (data.error) {
            alert(data.error);
            return;
        }
        chatDocuments[currentChat] = chatDocuments[currentChat] || [];
        chatDocuments[currentChat].push({ name: file.name, file: file });
        renderChatDocs();
        fileInput.value = "";
        if (data.message) alert(data.message);
    } catch (err) {
        console.error("Chat document upload error:", err);
        alert("Upload failed. Is the backend running?");
    }
}

function openDocPreview(file) {
    if (!file) {
        alert("Preview is not available for documents uploaded in a previous session.");
        return;
    }
    var url = URL.createObjectURL(file);
    window.open(url, "_blank", "noopener");
    setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
}

function toggleGlobalDocsList() {
    var list = document.getElementById("globalDocs");
    list.classList.toggle("doc-list-collapsed");
}

function toggleChatDocsList() {
    var list = document.getElementById("chatDocs");
    list.classList.toggle("doc-list-collapsed");
}

function renderGlobalDocs() {
    var list = document.getElementById("globalDocs");
    var toggleBtn = document.getElementById("globalDocsToggle");
    if (!list || !toggleBtn) return;
    var n = globalDocuments.length;
    toggleBtn.textContent = "Documents uploaded " + n;
    list.innerHTML = "";
    list.classList.add("doc-list-collapsed");
    globalDocuments.forEach(function (doc) {
        var li = document.createElement("li");
        li.className = "doc-item";
        var link = document.createElement("a");
        link.className = "doc-link";
        link.textContent = doc.name;
        link.title = doc.file ? "Open preview" : "Preview not available for previous session";
        link.href = "#";
        link.onclick = function (e) {
            e.preventDefault();
            openDocPreview(doc.file);
        };
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "doc-remove-btn";
        removeBtn.innerHTML = "&times;";
        removeBtn.title = "Remove document";
        removeBtn.onclick = function (e) {
            e.stopPropagation();
            removeGlobalDoc(doc.name);
        };
        li.appendChild(link);
        li.appendChild(removeBtn);
        list.appendChild(li);
    });
}

function removeGlobalDoc(docName) {
    if (!confirm("Do you want to remove this document?")) return;
    globalDocuments = globalDocuments.filter(function (d) { return d.name !== docName; });
    renderGlobalDocs();
}

function renderChatDocs() {
    if (loginMode !== "personal") return;
    var list = document.getElementById("chatDocs");
    var toggleBtn = document.getElementById("chatDocsToggle");
    if (!toggleBtn || !list) return;
    var docs = currentChat ? chatDocuments[currentChat] || [] : [];
    var n = docs.length;
    toggleBtn.textContent = "Documents uploaded " + n;
    list.innerHTML = "";
    list.classList.add("doc-list-collapsed");
    if (!currentChat) return;
    docs.forEach(function (doc) {
        var li = document.createElement("li");
        li.className = "doc-item chat-doc-item";
        var link = document.createElement("a");
        link.className = "doc-link";
        link.textContent = doc.name;
        link.title = doc.file ? "Open preview" : "Preview not available for previous session";
        link.href = "#";
        link.onclick = function (e) {
            e.preventDefault();
            openDocPreview(doc.file);
        };
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "doc-remove-btn";
        removeBtn.innerHTML = "&times;";
        removeBtn.title = "Remove document";
        removeBtn.onclick = function (e) {
            e.stopPropagation();
            removeChatDoc(doc.name);
        };
        li.appendChild(link);
        li.appendChild(removeBtn);
        list.appendChild(li);
    });
}

function removeChatDoc(docName) {
    if (!confirm("Do you want to remove this document?")) return;
    if (!currentChat) return;
    chatDocuments[currentChat] = chatDocuments[currentChat].filter(function (d) { return d.name !== docName; });
    renderChatDocs();
}

/* ---------------- SEND MESSAGE ---------------- */

function sendMessage() {

    const input = document.getElementById("messageInput");
    const message = input.value;

    if (!message || !message.trim()) return;

    if (!currentChat) {
        alert("Create or select a chat first");
        return;
    }

    addMessage(message, "user");
    input.value = "";

    fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            mode: loginMode,
            email: userEmail,
            chat: currentChat,
            message: message
        })
    })
    .then(function (res) {
        var ct = res.headers.get("Content-Type") || "";
        if (!ct.includes("application/json")) {
            if (!res.ok) {
                return res.text().then(function (t) {
                    throw new Error(res.status + " " + (t || res.statusText));
                });
            }
            throw new Error("Server did not return JSON.");
        }
        return res.json().then(function (data) {
            if (!res.ok) {
                var msg = data.message || res.status + " " + res.statusText;
                if (Array.isArray(data.detail)) {
                    msg = data.detail.map(function (d) { return d.msg || JSON.stringify(d); }).join("; ");
                } else if (data.detail) {
                    msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
                }
                throw new Error(msg);
            }
            return data;
        });
    })
    .then(function (data) {
        var reply = (data && data.reply != null) ? String(data.reply) : "No reply from server.";
        addMessage(reply, "bot");
    })
    .catch(function (error) {
        console.error("Error:", error);
        addMessage("Error: " + (error.message || "Server unreachable. Is the backend running on http://localhost:8000?"), "bot");
    });
}

function addMessage(text, role) {
    const chatArea = document.getElementById("chatArea");

    const wrapper = document.createElement("div");
    wrapper.style.display = "flex";
    wrapper.style.alignItems = "flex-start";
    wrapper.style.marginBottom = "10px";

    const avatar = document.createElement("img");
    avatar.className = "message-avatar";

    if (role === "user") {
        const profileImg = document.getElementById("profilePhoto");
        avatar.src = profileImg ? profileImg.src : "https://api.dicebear.com/7.x/avataaars/svg?seed=guest";
        wrapper.style.justifyContent = "flex-end";
    } else {
        avatar.src = "https://cdn-icons-png.flaticon.com/512/4712/4712027.png";
    }

    const messageDiv = document.createElement("div");
    messageDiv.className = "message " + role;
    messageDiv.innerText = text;

    if (role === "user") {
        wrapper.appendChild(messageDiv);
        wrapper.appendChild(avatar);
    } else {
        wrapper.appendChild(avatar);
        wrapper.appendChild(messageDiv);
    }

    chatArea.appendChild(wrapper);
    chatArea.scrollTop = chatArea.scrollHeight;
}

async function uploadDocument() {

    const fileInput = document.getElementById("globalUpload");
    const file = fileInput.files[0];

    if (!file) {
        alert("Please select a file first");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("email", userEmail || "guest");

    try {
        const response = await fetch("http://localhost:8000/upload", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (!data.error && data.message) {
            globalDocuments.push({ name: file.name, file: file });
            renderGlobalDocs();
        }
        alert(data.message || data.error);

    } catch (error) {
        console.error("Upload error:", error);
        alert("Upload failed");
    }
}