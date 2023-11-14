Vue.use(VueVirtualScroller);
Vue.component("virtual-scroller", VueVirtualScroller.VirtualScroller);
const $body = document.querySelector("body");

document.addEventListener("click", (e) => {
  return;
  let $target = e.target;
  let isColor = false;

  if ($target.classList.contains("color")) {
    isColor = true;
  } else if (
    $target.parentElement &&
    $target.parentElement.classList.contains("color")
  ) {
    isColor = true;
    $target = $target.parentElement;
  } else {
    return;
  }

  const isDark = $target.classList.contains("is-dark");
  const name = $target.querySelector("[data-name]").textContent;
  const hex = $target.querySelector("[data-hex]").textContent;
  const $detail = document.createElement("div");
  const $clone = $target.cloneNode(true);
  $clone.classList.remove("resize-observer");
  $clone.style = "";
  $detail.classList.add("color-detail");

  const rect = $target.getBoundingClientRect();
  $body.classList.add("show-color");

  $clone.style.top = rect.top + "px";
  $clone.style.left = "50%";
  $clone.style.height = rect.height + "px";
  $clone.style.width = rect.width + "px";
  $clone.style.background = hex;

  $body.append($clone);
  $clone.classList.add("keep");

  let targetRect = {
    width: Math.min(rect.width, 500),
    height: Math.min(window.innerHeight, 600),
  };

  setTimeout(() => {
    $clone.classList.add("squeeze");
    $clone.style.width = targetRect.width + "px";
  }, 300);
});

const app = new Vue({
  el: "#app",
  data: () => ({
    colors: [
      {
        hex: "#ffffff",
        rgb: { r: 255, g: 255, b: 255 },
        name: `Loading tons of colors`,
      },
    ],

    filter: "",
  }),

  computed: {
    filteredColors: function () {
      const lowerd = this.filter.toLowerCase();
      let colors = this.colors.filter((col) => {
        return (
          col.name.toLowerCase().indexOf(lowerd) > -1 ||
          col.hex.indexOf(lowerd) > -1
        );
      });
      return colors.length
        ? colors
        : [
            {
              hex: "#f0c",
              rgb: { r: 255, g: 255, b: 255 },
              name: `${this.colors.length} colors and you can't find any!`,
            },
          ];
    },
  },

  methods: {
    isDark: (rgb) =>
      Math.round(
        (parseInt(rgb.r) * 299 +
          parseInt(rgb.g) * 587 +
          parseInt(rgb.b) * 114) /
          1000
      ) < 125,

    show: (e) => {},
  },
});

const xhr = new XMLHttpRequest();
xhr.open("GET", "https://static.melaniebot.net/colorsforlist.json");
xhr.onload = (e) => {
  if (xhr.status === 200) {
    let resp = JSON.parse(xhr.responseText);
    app.colors =
      resp.colors /*.sort((a,b) => ( a.luminance - b.luminance )).reverse()*/;
  } else {
    console.log(xhr.status);
  }
};
xhr.send();
