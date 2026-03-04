let loginMode = "guest";
let userEmail = null;
let userUserId = null;  // User ID string (A1, C2...) for chat naming (A1.1, A1.2)
let chats = [];
let currentChat = null;
let globalDocuments = [];
let chatDocuments = {};
var API_BASE = "http://localhost:8000";

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

function changePhoto() {
    var input = document.getElementById("photoUpload");
    if (!input || !input.files || !input.files.length) return;
    var file = input.files[0];
    if (!file.type || !file.type.startsWith("image/")) {
        alert("Please choose an image file (e.g. JPG, PNG).");
        input.value = "";
        return;
    }
    var reader = new FileReader();
    reader.onload = function () {
        var profileImg = document.getElementById("profilePhoto");
        if (profileImg && reader.result) {
            profileImg.src = reader.result;
            if (userEmail) {
                try { localStorage.setItem("profilePhoto_" + userEmail, reader.result); } catch (e) {}
            }
        }
    };
    reader.readAsDataURL(file);
    input.value = "";
}

function loadSavedProfilePhoto() {
    if (!userEmail) return;
    var profileImg = document.getElementById("profilePhoto");
    if (!profileImg) return;
    try {
        var saved = localStorage.getItem("profilePhoto_" + userEmail);
        if (saved) profileImg.src = saved;
    } catch (e) {}
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
        // Fetch user_id (A1, C2, etc.) for profile and chat naming
        try {
            var infoRes = await fetch(API_BASE + "/user-info?email=" + encodeURIComponent(email));
            var info = await infoRes.json();
            userUserId = info.user_id || null;
            var dispEl = document.getElementById("profileUserId");
            if (dispEl) dispEl.textContent = userUserId || "--";
        } catch (e) {
            console.error("Failed to load user info", e);
        }

        // Load chats
        const chatRes = await fetch(API_BASE + "/chats/" + encodeURIComponent(email));
        const chatData = await chatRes.json();

        chats = (chatData.chats || []).map(function (c) { return typeof c === "string" ? c : c.name; });
        renderChats();

        // Load global documents (only for personal mode) - backend returns only global (chat_id null)
        if (loginMode === "personal") {
            const docRes = await fetch(`${API_BASE}/documents/${email}`);
            const docData = await docRes.json();
            const docList = docData.documents || [];
            globalDocuments = docList.map(function (d) { return { id: d.id, name: d.name, file: null, has_preview: d.has_preview }; });
            renderGlobalDocs();

            // Load chat documents for each chat so count/list show after re-login
            for (let i = 0; i < chats.length; i++) {
                const chatName = chats[i];
                try {
                    const chatDocRes = await fetch(`${API_BASE}/documents/${email}/${encodeURIComponent(chatName)}`);
                    const chatDocData = await chatDocRes.json();
                    const chatDocList = chatDocData.documents || [];
                    chatDocuments[chatName] = chatDocList.map(function (d) { return { id: d.id, name: d.name, file: null, has_preview: d.has_preview }; });
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
    loadSavedProfilePhoto();

    // Load existing chats & documents (and user id)
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
    loadSavedProfilePhoto();

    // Load existing chats (company mode) and user id
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
    userUserId = null;
    var dispEl = document.getElementById("profileUserId");
    if (dispEl) dispEl.textContent = "--";
    renderGlobalDocs();
}

/* ---------------- CHAT LOGIC ---------------- */

async function createChat() {
    if (!userEmail) {
        alert("Please sign in first");
        return;
    }
    // Refresh user_id from server (e.g. after first message created the user)
    if (!userUserId) {
        try {
            var r = await fetch(API_BASE + "/user-info?email=" + encodeURIComponent(userEmail));
            var info = await r.json();
            userUserId = info.user_id || null;
            var dispEl = document.getElementById("profileUserId");
            if (dispEl) dispEl.textContent = userUserId || "--";
        } catch (e) {}
    }
    // Name: "New Chat 1", "New Chat 2", ... (next unused number)
    var n = 1;
    while (chats.indexOf("New Chat " + n) !== -1) n++;
    var chatName = "New Chat " + n;
    try {
        var createRes = await fetch(API_BASE + "/chats", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: userEmail, name: chatName, mode: loginMode || "personal" })
        });
        var createData = await createRes.json();
        if (!createRes.ok && createRes.status !== 200) {
            alert(createData.detail || "Failed to create chat");
            return;
        }
    } catch (err) {
        console.error("Create chat error", err);
        alert("Failed to create chat. Is the backend running?");
        return;
    }
    chats.push(chatName);
    chatDocuments[chatName] = [];
    renderChats();
    await selectChat(chatName);
}

function renderChats() {
    const chatList = document.getElementById("chatList");
    chatList.innerHTML = "";

    chats.forEach(chat => {
        const li = document.createElement("li");
        li.className = "chat-list-item";
        li.setAttribute("data-chat-name", chat);

        const nameWrap = document.createElement("span");
        nameWrap.className = "chat-name-wrap";
        const nameSpan = document.createElement("span");
        nameSpan.className = "chat-name-text";
        nameSpan.textContent = chat;
        nameWrap.appendChild(nameSpan);

        const actions = document.createElement("div");
        actions.className = "chat-item-actions";
        const menuBtn = document.createElement("button");
        menuBtn.type = "button";
        menuBtn.className = "chat-menu-btn";
        menuBtn.setAttribute("aria-label", "Chat options");
        menuBtn.innerHTML = "&#8942;";
        const dropdown = document.createElement("div");
        dropdown.className = "chat-menu-dropdown";
        dropdown.setAttribute("role", "menu");
        const renameBtn = document.createElement("button");
        renameBtn.type = "button";
        renameBtn.className = "chat-menu-rename";
        renameBtn.textContent = "Rename";
        renameBtn.setAttribute("role", "menuitem");
        dropdown.appendChild(renameBtn);
        actions.appendChild(menuBtn);
        actions.appendChild(dropdown);

        li.appendChild(nameWrap);
        li.appendChild(actions);

        nameWrap.onclick = function (e) { e.stopPropagation(); selectChat(chat); };
        menuBtn.onclick = function (e) {
            e.stopPropagation();
            closeAllChatMenus();
            dropdown.classList.toggle("open");
        };
        renameBtn.onclick = function (e) {
            e.stopPropagation();
            dropdown.classList.remove("open");
            startRenameChat(li, chat, nameSpan);
        };

        chatList.appendChild(li);
    });
}

function closeAllChatMenus() {
    document.querySelectorAll(".chat-menu-dropdown.open").forEach(function (el) { el.classList.remove("open"); });
}

function startRenameChat(li, oldName, nameSpan) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "chat-name-edit";
    input.value = oldName;
    const wrap = li.querySelector(".chat-name-wrap");
    wrap.replaceChild(input, nameSpan);
    input.focus();
    input.select();

    function finishRename() {
        const newName = input.value.trim();
        wrap.removeChild(input);
        const span = document.createElement("span");
        span.className = "chat-name-text";
        span.textContent = newName ? newName : oldName;
        wrap.appendChild(span);
        span.onclick = function (e) { e.stopPropagation(); selectChat(newName || oldName); };

        if (newName && newName !== oldName) {
            renameChatOnServer(oldName, newName);
        }
        input.removeEventListener("blur", finishRename);
        input.removeEventListener("keydown", onKey);
    }

    function onKey(e) {
        if (e.key === "Enter") { e.preventDefault(); finishRename(); }
        if (e.key === "Escape") {
            e.preventDefault();
            input.value = oldName;
            finishRename();
        }
    }

    input.addEventListener("blur", finishRename);
    input.addEventListener("keydown", onKey);
}

async function renameChatOnServer(oldName, newName) {
    if (!userEmail) return;
    try {
        const res = await fetch(API_BASE + "/chats/rename", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: userEmail, old_name: oldName, new_name: newName })
        });
        const data = await res.json();
        if (res.ok && data.ok) {
            const idx = chats.indexOf(oldName);
            if (idx !== -1) chats[idx] = newName;
            if (currentChat === oldName) {
                currentChat = newName;
                document.getElementById("chatTitle").innerText = newName;
            }
            if (chatDocuments[oldName] !== undefined) {
                chatDocuments[newName] = chatDocuments[oldName];
                delete chatDocuments[oldName];
            }
            renderChats();
        } else {
            const msg = Array.isArray(data.detail) ? data.detail.map(function (x) { return x.msg || x; }).join(" ") : (data.detail || "Rename failed");
            alert(msg);
        }
    } catch (err) {
        console.error("Rename error", err);
        alert("Rename failed. Is the backend running?");
    }
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
                const chatDocRes = await fetch(`${API_BASE}/documents/${userEmail}/${encodeURIComponent(chatName)}`);
                const chatDocData = await chatDocRes.json();
                const chatDocList = chatDocData.documents || [];
                chatDocuments[chatName] = chatDocList.map(function (d) { return { id: d.id, name: d.name, file: null, has_preview: d.has_preview }; });
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
        chatDocuments[currentChat].push({ id: data.document_id, name: file.name, file: file, has_preview: true });
        renderChatDocs();
        fileInput.value = "";
        if (data.message) alert(data.message);
    } catch (err) {
        console.error("Chat document upload error:", err);
        alert("Upload failed. Is the backend running?");
    }
}

function openDocPreview(doc) {
    if (!doc) return;
    if ((doc.id && doc.has_preview !== false) && userEmail) {
        var url = API_BASE + "/documents/file/" + doc.id + "?email=" + encodeURIComponent(userEmail);
        window.open(url, "_blank", "noopener");
        return;
    }
    if (doc.file) {
        var blobUrl = URL.createObjectURL(doc.file);
        window.open(blobUrl, "_blank", "noopener");
        setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 60000);
        return;
    }
    alert("Preview is not available for this document.");
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
        link.title = (doc.has_preview || doc.file || (doc.id && doc.has_preview !== false)) ? "Open preview" : "Preview not available";
        link.href = "#";
        link.onclick = function (e) {
            e.preventDefault();
            openDocPreview(doc);
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
        link.title = (doc.has_preview || doc.file || (doc.id && doc.has_preview !== false)) ? "Open preview" : "Preview not available";
        link.href = "#";
        link.onclick = function (e) {
            e.preventDefault();
            openDocPreview(doc);
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

(function setupEnterToSend() {
    var input = document.getElementById("messageInput");
    if (input) {
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                sendMessage();
            }
        });
    }
})();

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
            globalDocuments.push({ id: data.document_id, name: file.name, file: file, has_preview: true });
            renderGlobalDocs();
        }
        alert(data.message || data.error);

    } catch (error) {
        console.error("Upload error:", error);
        alert("Upload failed");
    }
}

// Close chat dropdowns when clicking outside
document.addEventListener("click", function () {
    closeAllChatMenus();
});