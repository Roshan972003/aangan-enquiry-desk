(function(){var K="aangan-theme",t="dark";try{t=localStorage.getItem(K)||"dark"}catch(e){}
document.documentElement.setAttribute("data-theme",t);
window.addEventListener("DOMContentLoaded",function(){var nav=document.querySelector(".nav");if(!nav)return;var b=document.createElement("button");b.className="ghost themebtn";b.id="theme";
function paint(){b.textContent=document.documentElement.getAttribute("data-theme")==="dark"?"Light mode":"Dark mode"}
b.onclick=function(){var n=document.documentElement.getAttribute("data-theme")==="dark"?"light":"dark";document.documentElement.setAttribute("data-theme",n);try{localStorage.setItem(K,n)}catch(e){}paint()};
paint();nav.appendChild(b)})})();
