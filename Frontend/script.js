let loginMode = "guest";
let userEmail = null;
let userUserId = null;  // User ID string (A1, C2...) for chat naming (A1.1, A1.2)
let userIsAdmin = false;
var SUPER_ADMIN_EMAIL = "parshant786yadav@gmail.com";
function isSuperAdmin() {
    return !!(userEmail && (userEmail + "").trim().toLowerCase() === SUPER_ADMIN_EMAIL);
}
let dbData = null;  // admin database view data
let chats = [];
let currentChat = null;
let globalDocuments = [];
let chatDocuments = {};
let companyDocuments = [];  // HR company docs (same-domain users access via chat only)
var API_BASE = (window.location.protocol === "http:" || window.location.protocol === "https:") ? "" : "http://localhost:8000";

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

var loginPopupMode = "personal";  // "personal" or "company"

function openLoginPopup() {
    document.getElementById("loginPopup").style.display = "flex";
    showLoginStep1();
}

function closeLoginPopup() {
    document.getElementById("loginPopup").style.display = "none";
}

function showLoginStep1() {
    document.getElementById("loginStep1").style.display = "block";
    document.getElementById("loginStep2").style.display = "none";
}

function resetOtpStep() {
    document.getElementById("loginOtpSection").style.display = "none";
    document.getElementById("loginOtpInput").value = "";
    var btn = document.getElementById("loginEmailBtn");
    btn.textContent = "Send OTP";
    btn.onclick = doSendOtp;
}

function showPersonalLogin() {
    loginPopupMode = "personal";
    document.getElementById("loginStep1").style.display = "none";
    document.getElementById("loginStep2").style.display = "block";
    document.getElementById("loginStep2Title").textContent = "Personal Login";
    document.getElementById("loginEmailInput").placeholder = "Enter your email";
    document.getElementById("loginEmailInput").value = "";
    resetOtpStep();
    var input = document.getElementById("loginEmailInput");
    input.onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); doSendOtp(); } };
    input.focus();
}

function showCompanyLogin() {
    loginPopupMode = "company";
    document.getElementById("loginStep1").style.display = "none";
    document.getElementById("loginStep2").style.display = "block";
    document.getElementById("loginStep2Title").textContent = "Company Login";
    document.getElementById("loginEmailInput").placeholder = "name@company.com";
    document.getElementById("loginEmailInput").value = "";
    resetOtpStep();
    var input = document.getElementById("loginEmailInput");
    input.onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); doSendOtp(); } };
    input.focus();
}

async function doSendOtp() {
    var email = (document.getElementById("loginEmailInput").value || "").trim();
    if (!email || !email.includes("@")) {
        alert(loginPopupMode === "company" ? "Enter valid company email" : "Enter valid email");
        return;
    }
    var btn = document.getElementById("loginEmailBtn");
    btn.disabled = true;
    btn.textContent = "Sending...";
    try {
        var res = await fetch(API_BASE + "/auth/send-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: email })
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) {
            alert(data.detail || "Failed to send OTP");
            btn.disabled = false;
            btn.textContent = "Send OTP";
            return;
        }
        document.getElementById("loginOtpSection").style.display = "block";
        btn.textContent = "Resend OTP";
        btn.onclick = doSendOtp;
        btn.disabled = false;
        document.getElementById("loginOtpInput").value = "";
        document.getElementById("loginOtpInput").focus();
        document.getElementById("loginVerifyOtpBtn").onclick = doVerifyOtp;
        document.getElementById("loginOtpInput").onkeydown = function (e) {
            if (e.key === "Enter") { e.preventDefault(); doVerifyOtp(); }
        };
    } catch (e) {
        console.error(e);
        alert("Failed to send OTP. Check your connection.");
        btn.disabled = false;
        btn.textContent = "Send OTP";
    }
}

async function doVerifyOtp() {
    var email = (document.getElementById("loginEmailInput").value || "").trim();
    var otp = (document.getElementById("loginOtpInput").value || "").trim();
    if (!email || !email.includes("@")) {
        alert("Enter valid email first.");
        return;
    }
    if (!otp || otp.length < 4) {
        alert("Enter the 6-digit OTP from your email.");
        return;
    }
    try {
        var res = await fetch(API_BASE + "/auth/verify-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: email, otp: otp, mode: loginPopupMode || "personal" })
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) {
            alert(data.detail || "Invalid or expired OTP");
            return;
        }
        closeLoginPopup();
        if (loginPopupMode === "personal") {
            loginMode = "personal";
            userEmail = email;
            applyPersonalLoginUI(email);
        } else {
            loginMode = "company";
            userEmail = email;
            applyCompanyLoginUI(email);
        }
        loadSavedProfilePhoto();
        loadUserData(email);
    } catch (e) {
        console.error(e);
        alert("Verification failed. Check your connection.");
    }
}

function googleLoginFromPopup() {
    closeLoginPopup();
    googleLogin();
}

/* ================= LOAD USER DATA ================= */

async function loadUserData(email) {
    try {
        // Fetch user_id (A1, C2, etc.) for profile and chat naming
        try {
            var infoRes = await fetch(API_BASE + "/user-info?email=" + encodeURIComponent(email));
            var info = await infoRes.json();
            userUserId = info.user_id || null;
            userIsAdmin = !!info.is_admin;
            var dispEl = document.getElementById("profileUserId");
            if (dispEl) dispEl.textContent = userUserId || "--";
            var adminSec = document.getElementById("adminSection");
            if (adminSec) adminSec.style.display = userIsAdmin ? "block" : "none";
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
        }
        // Load company documents for HR (company mode, hr@...)
        if (loginMode === "company" && email && email.trim().toLowerCase().startsWith("hr@")) {
            try {
                const companyRes = await fetch(`${API_BASE}/documents/company/${encodeURIComponent(email)}`);
                const companyData = await companyRes.json();
                companyDocuments = (companyData.documents || []).map(function (d) { return { id: d.id, name: d.name, has_preview: d.has_preview }; });
                renderCompanyDocs();
                var setRes = await fetch(`${API_BASE}/company/settings?email=${encodeURIComponent(email)}`);
                var setData = await setRes.json().catch(function () { return {}; });
                var check = document.getElementById("companyShowCountCheck");
                if (check) check.checked = !!setData.show_doc_count_to_employees;
            } catch (e) {
                companyDocuments = [];
            }
        }
        // Load company document count for employees (when HR enabled "show count to employees")
        if (loginMode === "company" && email && !email.trim().toLowerCase().startsWith("hr@")) {
            try {
                const countRes = await fetch(`${API_BASE}/documents/company/count?email=${encodeURIComponent(email)}`);
                const countData = await countRes.json().catch(function () { return {}; });
                var countEl = document.getElementById("companyDocCountEmployee");
                if (countEl && countData.visible) {
                    countEl.textContent = "Company documents: " + (countData.count || 0);
                    countEl.style.display = "block";
                }
            } catch (e) {}
        }

        if (loginMode === "personal") {
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


/* ================= PERSONAL / COMPANY LOGIN UI ================= */

function applyPersonalLoginUI(email) {
    document.getElementById("loginBtn").style.display = "none";
    document.getElementById("profileBox").style.display = "block";
    var nameEl = document.getElementById("profileName");
    nameEl.textContent = email;
    nameEl.title = email;
    document.getElementById("documentSection").style.display = "block";
    document.getElementById("documentSectionTitle").textContent = "Global Documents";
    document.getElementById("documentUploadWrap").style.display = "block";
    document.getElementById("globalDocsBlock").style.display = "block";
    var companyDocsBlock = document.getElementById("companyDocsBlock");
    if (companyDocsBlock) companyDocsBlock.style.display = "none";
    var companyShowCountWrap = document.getElementById("companyShowCountWrap");
    if (companyShowCountWrap) companyShowCountWrap.style.display = "none";
    document.getElementById("companyDocCountEmployee").style.display = "none";
    document.getElementById("companyDocHintHr").style.display = "none";
    document.getElementById("companyDocHintEmployee").style.display = "none";
}

function applyCompanyLoginUI(email) {
    document.getElementById("loginBtn").style.display = "none";
    document.getElementById("profileBox").style.display = "block";
    var nameEl = document.getElementById("profileName");
    nameEl.textContent = email;
    nameEl.title = email;
    document.getElementById("documentSection").style.display = "block";
    document.getElementById("documentSectionTitle").textContent = "Company Documents";
    document.getElementById("globalDocsBlock").style.display = "none";
    var isHr = email && String(email).trim().toLowerCase().startsWith("hr@");
    document.getElementById("documentUploadWrap").style.display = isHr ? "block" : "none";
    document.getElementById("companyDocHintHr").style.display = isHr ? "block" : "none";
    var companyShowCountWrap = document.getElementById("companyShowCountWrap");
    if (companyShowCountWrap) companyShowCountWrap.style.display = isHr ? "block" : "none";
    var companyDocsBlock = document.getElementById("companyDocsBlock");
    if (companyDocsBlock) companyDocsBlock.style.display = isHr ? "block" : "none";
    document.getElementById("companyDocCountEmployee").style.display = "none";
    document.getElementById("companyDocHintEmployee").style.display = isHr ? "none" : "block";
    var panel = document.getElementById("chatDocsPanel");
    if (panel) panel.style.display = "none";
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
    companyDocuments = [];
    currentChat = null;

    document.getElementById("chatList").innerHTML = "";
    document.getElementById("chatArea").innerHTML = "";
    document.getElementById("chatTitle").innerText = "Select or Create Chat";
    var panel = document.getElementById("chatDocsPanel");
    if (panel) panel.style.display = "none";
    document.getElementById("documentSection").style.display = "none";
    userUserId = null;
    userIsAdmin = false;
    var dispEl = document.getElementById("profileUserId");
    if (dispEl) dispEl.textContent = "--";
    var adminSec = document.getElementById("adminSection");
    if (adminSec) adminSec.style.display = "none";
    closeDatabaseView();
    renderGlobalDocs();
}

/* ---------------- CHAT LOGIC ---------------- */

/** Creates a new chat on server and in local state. Returns the new chat name or null on failure. Does not select it. */
async function createNewChatAndReturnName() {
    if (!userEmail) return null;
    if (!userUserId) {
        try {
            var r = await fetch(API_BASE + "/user-info?email=" + encodeURIComponent(userEmail));
            var info = await r.json();
            userUserId = info.user_id || null;
            var dispEl = document.getElementById("profileUserId");
            if (dispEl) dispEl.textContent = userUserId || "--";
        } catch (e) {}
    }
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
        if (!createRes.ok && createRes.status !== 200) return null;
    } catch (err) {
        console.error("Create chat error", err);
        return null;
    }
    chats.push(chatName);
    chatDocuments[chatName] = [];
    return chatName;
}

async function createChat() {
    if (!userEmail) {
        alert("Please sign in first");
        return;
    }
    var chatName = await createNewChatAndReturnName();
    if (!chatName) {
        alert("Failed to create chat. Is the backend running?");
        return;
    }
    renderChats();
    await selectChat(chatName);
}

function renderChats() {
    const chatList = document.getElementById("chatList");
    chatList.innerHTML = "";

    chats.forEach(chat => {
        const li = document.createElement("li");
        li.className = "chat-list-item" + (chat === currentChat ? " chat-list-item-active" : "");
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
    closeDatabaseView();
    currentChat = chatName;
    document.getElementById("chatTitle").innerText = chatName;
    document.getElementById("chatArea").innerHTML = "";
    renderChats();

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
    var ok = file.type === "application/pdf" || (file.type && file.type.indexOf("image/") === 0);
    if (!ok) {
        alert("Only PDF and images (JPG, PNG, GIF, etc.) are supported");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("email", userEmail || "guest");
    formData.append("chat", currentChat);
    formData.append("mode", loginMode === "company" ? "company" : "personal");

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
            removeGlobalDoc(doc.id);
        };
        li.appendChild(link);
        li.appendChild(removeBtn);
        list.appendChild(li);
    });
}

async function removeGlobalDoc(docId) {
    if (!confirm("Do you want to remove this document?")) return;
    if (!userEmail) return;
    try {
        var res = await fetch(API_BASE + "/documents/" + docId + "?email=" + encodeURIComponent(userEmail), { method: "DELETE" });
        if (!res.ok) {
            var data = await res.json().catch(function () { return {}; });
            alert(data.detail || "Could not delete document");
            return;
        }
    } catch (e) {
        console.error(e);
        alert("Could not delete document");
        return;
    }
    globalDocuments = globalDocuments.filter(function (d) { return d.id !== docId; });
    renderGlobalDocs();
}

function toggleCompanyDocsList() {
    var list = document.getElementById("companyDocsList");
    if (list) list.classList.toggle("doc-list-collapsed");
}

function renderCompanyDocs() {
    var list = document.getElementById("companyDocsList");
    var toggleBtn = document.getElementById("companyDocsToggle");
    if (!list || !toggleBtn) return;
    var n = companyDocuments.length;
    toggleBtn.textContent = "Documents uploaded " + n;
    list.innerHTML = "";
    list.classList.add("doc-list-collapsed");
    companyDocuments.forEach(function (doc) {
        var li = document.createElement("li");
        li.className = "doc-item";
        var link = document.createElement("a");
        link.className = "doc-link";
        link.textContent = doc.name;
        link.title = (doc.has_preview !== false) ? "Open preview" : "Preview not available";
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
            removeCompanyDoc(doc.id);
        };
        li.appendChild(link);
        li.appendChild(removeBtn);
        list.appendChild(li);
    });
}

async function removeCompanyDoc(docId) {
    if (!confirm("Do you want to remove this document?")) return;
    if (!userEmail) return;
    try {
        var res = await fetch(API_BASE + "/documents/" + docId + "?email=" + encodeURIComponent(userEmail), { method: "DELETE" });
        if (!res.ok) {
            var data = await res.json().catch(function () { return {}; });
            alert(data.detail || "Could not delete document");
            return;
        }
    } catch (e) {
        console.error(e);
        alert("Could not delete document");
        return;
    }
    companyDocuments = companyDocuments.filter(function (d) { return d.id !== docId; });
    renderCompanyDocs();
}

async function toggleCompanyShowCountToEmployees() {
    var check = document.getElementById("companyShowCountCheck");
    if (!check || !userEmail) return;
    try {
        var res = await fetch(API_BASE + "/company/settings", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: userEmail, show_doc_count_to_employees: check.checked })
        });
        if (!res.ok) {
            var data = await res.json().catch(function () { return {}; });
            alert(data.detail || "Could not update setting");
            check.checked = !check.checked;
        }
    } catch (e) {
        console.error(e);
        check.checked = !check.checked;
        alert("Could not update setting");
    }
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
            removeChatDoc(doc.id);
        };
        li.appendChild(link);
        li.appendChild(removeBtn);
        list.appendChild(li);
    });
}

async function removeChatDoc(docId) {
    if (!confirm("Do you want to remove this document?")) return;
    if (!currentChat || !userEmail) return;
    try {
        var res = await fetch(API_BASE + "/documents/" + docId + "?email=" + encodeURIComponent(userEmail), { method: "DELETE" });
        if (!res.ok) {
            var data = await res.json().catch(function () { return {}; });
            alert(data.detail || "Could not delete document");
            return;
        }
    } catch (e) {
        console.error(e);
        alert("Could not delete document");
        return;
    }
    chatDocuments[currentChat] = (chatDocuments[currentChat] || []).filter(function (d) { return d.id !== docId; });
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

async function sendMessage() {

    const input = document.getElementById("messageInput");
    const message = input.value;

    if (!message || !message.trim()) return;

    if (!currentChat) {
        if (!userEmail) {
            alert("Please sign in first");
            return;
        }
        var newName = await createNewChatAndReturnName();
        if (!newName) {
            alert("Could not create chat. Is the backend running?");
            return;
        }
        currentChat = newName;
        document.getElementById("chatTitle").innerText = currentChat;
        var panel = document.getElementById("chatDocsPanel");
        if (loginMode === "personal" && panel) {
            panel.style.display = "block";
            renderChatDocs();
        }
        renderChats();
    }

    addMessage(message, "user");
    input.value = "";
    addTypingIndicator();

    fetch(API_BASE + "/chat", {
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
        removeTypingIndicator();
        var reply = (data && data.reply != null) ? String(data.reply) : "No reply from server.";
        addMessage(reply, "bot");
    })
    .catch(function (error) {
        console.error("Error:", error);
        removeTypingIndicator();
        addMessage("Error: " + (error.message || "Server unreachable. Is the backend running on http://localhost:8000?"), "bot");
    });
}

function addTypingIndicator() {
    var chatArea = document.getElementById("chatArea");
    if (!chatArea) return;
    var wrapper = document.createElement("div");
    wrapper.className = "typing-indicator-wrapper";
    wrapper.setAttribute("data-typing", "1");
    wrapper.style.display = "flex";
    wrapper.style.alignItems = "flex-start";
    wrapper.style.marginBottom = "10px";
    var avatar = document.createElement("img");
    avatar.className = "message-avatar";
    avatar.src = "https://cdn-icons-png.flaticon.com/512/4712/4712027.png";
    var messageDiv = document.createElement("div");
    messageDiv.className = "message bot typing-indicator";
    messageDiv.innerText = "typing...";
    wrapper.appendChild(avatar);
    wrapper.appendChild(messageDiv);
    chatArea.appendChild(wrapper);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function removeTypingIndicator() {
    var el = document.querySelector(".typing-indicator-wrapper");
    if (el) el.remove();
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

    if (!userEmail) {
        alert("Please sign in first");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("email", userEmail);
    formData.append("mode", loginMode === "company" ? "company" : "personal");
    // Global/company uploads: no chat. (Chat-specific docs use the Chat Documents panel in personal mode.)

    try {
        const response = await fetch(API_BASE + "/upload", {
            method: "POST",
            body: formData
        });

        var data = {};
        try {
            var ct = response.headers.get("Content-Type") || "";
            if (ct.includes("application/json")) {
                data = await response.json();
            }
        } catch (e) {
            console.error("Upload response parse error", e);
        }

        if (!response.ok) {
            alert(data.detail || data.error || "Upload failed");
            fileInput.value = "";
            return;
        }
        if (data.error) {
            alert(data.error);
            fileInput.value = "";
            return;
        }
        if (loginMode === "personal" && data.message) {
            globalDocuments.push({ id: data.document_id, name: file.name, file: file, has_preview: true });
            renderGlobalDocs();
        }
        if (loginMode === "company") {
            if (data.document_id != null) {
                companyDocuments.push({ id: data.document_id, name: file.name, has_preview: true });
                renderCompanyDocs();
            }
            alert("Document uploaded. Everyone with your company email domain can ask questions about it in chat.");
        } else {
            alert(data.message || "Uploaded");
        }
        fileInput.value = "";
    } catch (error) {
        console.error("Upload error:", error);
        alert("Upload failed. Check the console and that the backend is running.");
    }
}

/* ---------------- ADMIN DATABASE VIEW ---------------- */

function openDatabaseView() {
    if (!userEmail || !userIsAdmin) return;
    var mainPanel = document.getElementById("mainChatPanel");
    var dbView = document.getElementById("databaseView");
    if (mainPanel) mainPanel.style.display = "none";
    if (dbView) dbView.style.display = "flex";
    fetchDatabaseAndAdmins();
}

function closeDatabaseView() {
    var mainPanel = document.getElementById("mainChatPanel");
    var dbView = document.getElementById("databaseView");
    if (mainPanel) mainPanel.style.display = "flex";
    if (dbView) dbView.style.display = "none";
}

async function fetchDatabaseAndAdmins() {
    if (!userEmail) return;
    try {
        var dbRes = await fetch(API_BASE + "/admin/database?email=" + encodeURIComponent(userEmail));
        if (!dbRes.ok) {
            alert("Admin access denied or error loading data.");
            return;
        }
        dbData = await dbRes.json();
        var superAdmin = isSuperAdmin();
        var addSection = document.getElementById("adminAddAdminSection");
        if (addSection) addSection.style.display = superAdmin ? "block" : "none";
        if (superAdmin) {
            var adminsRes = await fetch(API_BASE + "/admin/admins?email=" + encodeURIComponent(userEmail));
            if (adminsRes.ok) {
                var adminsData = await adminsRes.json();
                renderAdminListWithRemove(adminsData.admins || [], true);
            } else {
                renderAdminListWithRemove([], true);
            }
        } else {
            var toggleBtn = document.getElementById("adminListToggle");
            var listEl = document.getElementById("adminListCollapsible");
            if (toggleBtn) toggleBtn.style.display = "none";
            if (listEl) listEl.innerHTML = "";
        }
        renderDbTab();
        setupDbTabs();
        setupDbSearch();
    } catch (e) {
        console.error("Error loading admin data", e);
        alert("Could not load database. Is the backend running?");
    }
}

function setupDbTabs() {
    document.querySelectorAll(".db-tab").forEach(function (btn) {
        btn.onclick = function () {
            document.querySelectorAll(".db-tab").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            renderDbTab();
        };
    });
}

function renderDbTab() {
    var active = document.querySelector(".db-tab.active");
    var tableName = active ? active.getAttribute("data-table") : "users";
    var container = document.getElementById("dbTableContent");
    var searchInput = document.getElementById("dbSearchInput");
    var searchTerm = (searchInput && searchInput.value) ? searchInput.value.trim().toLowerCase() : "";
    if (!container || !dbData) return;
    var rows = dbData[tableName] || [];
    if (searchTerm) {
        rows = rows.filter(function (row) {
            return Object.keys(row).some(function (k) {
                var v = row[k];
                if (v === null || v === undefined) return false;
                return String(v).toLowerCase().indexOf(searchTerm) !== -1;
            });
        });
    }
    if (rows.length === 0) {
        container.innerHTML = "<p class=\"db-empty\">No rows" + (searchTerm ? " matching search" : "") + "</p>";
        return;
    }
    var keys = Object.keys(rows[0]);
    var html = "<table class=\"db-table\"><thead><tr>";
    keys.forEach(function (k) {
        html += "<th>" + escapeHtml(k) + "</th>";
    });
    if (tableName === "documents") html += "<th>Actions</th>";
    html += "</tr></thead><tbody>";
    rows.forEach(function (row) {
        html += "<tr>";
        keys.forEach(function (k) {
            var v = row[k];
            if (v === null || v === undefined) v = "";
            var str = typeof v === "object" ? JSON.stringify(v) : String(v);
            if (str.length > 200) str = str.substring(0, 200) + "...";
            html += "<td>" + escapeHtml(str) + "</td>";
        });
        if (tableName === "documents" && row.id != null) {
            var docUrl = API_BASE + "/documents/file/" + row.id + "?email=" + encodeURIComponent(userEmail || "");
            html += "<td><a href=\"" + escapeHtml(docUrl) + "\" target=\"_blank\" rel=\"noopener\" class=\"db-doc-link\">View / Download</a></td>";
        }
        html += "</tr>";
    });
    html += "</tbody></table>";
    container.innerHTML = html;
}

function setupDbSearch() {
    var searchInput = document.getElementById("dbSearchInput");
    if (!searchInput) return;
    searchInput.value = "";
    searchInput.oninput = function () { renderDbTab(); };
}

function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    var div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
}

async function addAdminEmail() {
    var input = document.getElementById("newAdminEmail");
    if (!input || !userEmail) return;
    var newEmail = (input.value || "").trim();
    if (!newEmail || !newEmail.includes("@")) {
        alert("Enter a valid email address.");
        return;
    }
    try {
        var res = await fetch(API_BASE + "/admin/admins", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: userEmail, new_admin_email: newEmail })
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) {
            alert(data.detail || "Failed to add admin");
            return;
        }
        input.value = "";
        renderAdminListWithRemove(data.admins || [], true);
        alert(data.message || "Admin added.");
    } catch (e) {
        console.error("Add admin error", e);
        alert("Failed to add admin.");
    }
}

function toggleAdminList() {
    var list = document.getElementById("adminListCollapsible");
    if (list) list.classList.toggle("doc-list-collapsed");
}

function renderAdminListWithRemove(admins, showRemoveButtons) {
    var toggleBtn = document.getElementById("adminListToggle");
    var listEl = document.getElementById("adminListCollapsible");
    if (!toggleBtn || !listEl) return;
    var superAdmin = showRemoveButtons === true;
    var n = (admins && admins.length) || 0;
    toggleBtn.textContent = "Admins (" + n + ")";
    toggleBtn.style.display = "block";
    if (n === 0) {
        listEl.innerHTML = "<li class=\"admin-list-li admin-list-empty\">No admins listed.</li>";
        listEl.classList.add("doc-list-collapsed");
        return;
    }
    var html = "";
    admins.forEach(function (email) {
        html += "<li class=\"admin-list-li\"><span class=\"admin-email\">" + escapeHtml(email) + "</span>";
        if (superAdmin) {
            var safeEmail = escapeHtml(email).replace(/"/g, "&quot;");
            html += " <button type=\"button\" class=\"admin-remove-btn\" data-remove-email=\"" + safeEmail + "\">Remove</button>";
        }
        html += "</li>";
    });
    listEl.innerHTML = html;
    if (superAdmin) {
        listEl.querySelectorAll(".admin-remove-btn").forEach(function (btn) {
            btn.onclick = function () { removeAdminEmail(btn.getAttribute("data-remove-email")); };
        });
    }
}

async function removeAdminEmail(emailToRemove) {
    if (!userEmail || !emailToRemove) return;
    if (!confirm("Remove " + emailToRemove + " from admins?")) return;
    try {
        var res = await fetch(API_BASE + "/admin/admins/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: userEmail, remove_admin_email: emailToRemove })
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) {
            alert(data.detail || "Failed to remove admin");
            return;
        }
        renderAdminListWithRemove(data.admins || [], true);
        alert(data.message || "Admin removed.");
        if ((data.admins || []).indexOf(userEmail) === -1) {
            userIsAdmin = false;
            var adminSec = document.getElementById("adminSection");
            if (adminSec) adminSec.style.display = "none";
            closeDatabaseView();
        }
    } catch (e) {
        console.error("Remove admin error", e);
        alert("Failed to remove admin.");
    }
}

// Close chat dropdowns when clicking outside
document.addEventListener("click", function () {
    closeAllChatMenus();
});


function googleLogin() {
    window.location.href = API_BASE + "/login/google";
}

(function checkGoogleLoginCallback() {
    var params = new URLSearchParams(window.location.search);
    var email = params.get("email");
    if (!email) return;
    userEmail = email;
    loginMode = "personal";
    document.getElementById("loginBtn").style.display = "none";
    document.getElementById("profileBox").style.display = "block";
    document.getElementById("profileName").textContent = email;
    document.getElementById("profileName").title = email;
    document.getElementById("documentSection").style.display = "block";
    document.getElementById("documentSectionTitle").textContent = "Global Documents";
    document.getElementById("documentUploadWrap").style.display = "block";
    document.getElementById("globalDocsBlock").style.display = "block";
    var companyDocsBlock = document.getElementById("companyDocsBlock");
    if (companyDocsBlock) companyDocsBlock.style.display = "none";
    var companyShowCountWrap = document.getElementById("companyShowCountWrap");
    if (companyShowCountWrap) companyShowCountWrap.style.display = "none";
    document.getElementById("companyDocCountEmployee").style.display = "none";
    document.getElementById("companyDocHintHr").style.display = "none";
    document.getElementById("companyDocHintEmployee").style.display = "none";
    loadSavedProfilePhoto();
    loadUserData(email).catch(function (e) { console.error("Error loading user data after Google login", e); });
    history.replaceState({}, document.title, window.location.pathname || "/");
})();