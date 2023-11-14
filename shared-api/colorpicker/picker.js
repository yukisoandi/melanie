console.clear();

import * as THREE from "./three.module.js";
import { OrbitControls } from "./OrbitControls.js";
let cam,
  scene,
  root,
  renderer,
  controls,
  layers,
  objects = [],
  cubeSize = 100,
  dotSize = 1.25,
  width = window.innerWidth + 1,
  height = window.innerHeight + 1,
  $select = document.querySelector("[data-model]"),
  $selectList = document.querySelector("[data-list]"),
  cDark = "#202124",
  cLight = "#ffffff",
  bg = cDark,
  colorMode = "rgb",
  spaceCube,
  isDark = true;

const colorModes = {
  hsv: {
    func: "hsv",
    x: [0, 360],
    y: [1, 1],
    z: [2, 1],
  },

  hsi: {
    func: "hsi",
    x: [0, 360],
    y: [1, 1],
    z: [2, 1],
  },

  hsl: {
    func: "hsl",
    x: [0, 360],
    y: [1, 1],
    z: [2, 1],
  },

  rgb: {
    func: "rgb",
    x: [0, 255],
    y: [1, 255],
    z: [2, 255],
  },

  xyz: {
    func: "xyz",
    x: [0, 95.047],
    y: [1, 100],
    z: [2, 108.883],
  },

  cat02: {
    func: "cat02",
    x: [0, 95.047],
    y: [1, 104],
    z: [2, 108.883],
  },

  jab: {
    func: "jzazbz",
    x: [0, 0.2],
    y: [1, 0.16, -0.16],
    z: [2, 0.16, -0.16],
  },

  luv: {
    func: "luv",
    x: [0, 100],
    y: [1, 224, -134],
    z: [2, 122, -140],
  },

  lab: {
    func: "lab",
    z: [0, 100],
    y: [1, 127, -128],
    x: [2, 127, -128],
  },

  oklab: {
    func: "oklab",
    z: [0, 1],
    y: [1, 0.3, -0.3],
    x: [2, 0.35, -0.35],
  },

  lch: {
    func: "lch",
    z: [0, 100],
    y: [1, 100],
    x: [2, 0, 360],
  },

  yuv: {
    func: "yuv",
    z: [0, 255],
    y: [1, 255],
    x: [2, 255],
  },

  hwb: {
    func: "hwb",
    x: [0, 360],
    y: [1, 1],
    z: [2, 1],
  },

  hcg: {
    func: "hcg",
    x: [0, 360],
    y: [1, 1],
    z: [2, 1],
  },
};

init();

function onWindowResize() {
  width = window.innerWidth + 1;
  height = window.innerHeight + 1;
  cam.aspect = width / height;
  cam.updateProjectionMatrix();
  renderer.setSize(width, height);
}

let colorList = [];
/*
const xhr = new XMLHttpRequest();
xhr.open('GET', 'https://unpkg.com/color-name-list/dist/colornames.json');
xhr.onload = e => {
  if (xhr.status === 200) {
    colorList = JSON.parse(xhr.responseText);
    addParticles(colorList, colorMode);
  } else {
    console.log(xhr.status);
  }
};
xhr.send();*/

function fetchList(listname = "default") {
  fetch(`https://static.melaniebot.net/picker_${listname}.json`)
    .then((d) => d.json())
    .then((d) => {
      colorList = d.colors;
      addParticles(colorList, colorMode);
    });
}

fetchList();

let part;

function createCanvasMaterial(color, size = 256) {
  var matCanvas = document.createElement("canvas");
  matCanvas.width = matCanvas.height = size;
  var matContext = matCanvas.getContext("2d");
  // create exture object from canvas.
  var texture = new THREE.Texture(matCanvas);
  // Draw a circle
  var center = size / 2;

  matContext.beginPath();
  matContext.arc(center, center, size / 2, 0, 2 * Math.PI, false);
  matContext.closePath();
  matContext.fillStyle = color;
  matContext.fill();
  // need to set needsUpdate
  texture.needsUpdate = true;
  // return a texture made from the canvas
  return texture;
}

let pMaterial, particles;

function addParticles(colorNames, cMode) {
  // create the particle variables
  const particleCount = colorNames.length;

  if (particles) {
    particles.dispose();
  }

  particles = new THREE.Geometry();

  if (pMaterial) {
    pMaterial.dispose();
  }

  dotSize = (255 / Math.sqrt(colorNames.length / 3)) * 0.4;
  dotSize = Math.max(Math.min(dotSize, 4), 1.25);

  pMaterial = new THREE.PointsMaterial({
    vertexColors: THREE.VertexColors,
    size: dotSize,
    alphaMap: createCanvasMaterial("#ffffff", dotSize * 100),
    flatShading: true,
    //fog: false,
    //depthWrite: false,
    transparent: true,
    alphaTest: 0.5,
    //sizeAttenuation: true,
  });

  let colors = [];

  const mode = colorModes[cMode];

  colorNames.forEach((col, i) => {
    let colorComp;
    let color = new Color({
      color: col.hex,
      type: "hex",
    });

    if (mode.func === "oklab") {
      colorComp = linear_srgb_to_oklab(chroma(col.hex).rgb());
    } else if (mode.func === "yuv") {
      colorComp = yuv(chroma(col.hex).rgb());
    } else if (mode.func === "luv") {
      colorComp = color.luv;
    } else if (
      mode.func === "xyz" ||
      mode.func === "cat02" ||
      mode.func === "jzazbz"
    ) {
      colorComp = color.xyz;

      if (mode.func === "jzazbz") {
        colorComp = Jzazbz(colorComp);
      }

      if (mode.func === "cat02") {
        colorComp = xyz2cat02(colorComp);
      }
    } else if (mode.func === "hwb") {
      const [hsvH, hsvS, hsvV] = chroma(col.hex).hsv();
      colorComp = [hsvH, (1 - hsvS) * hsvV, 1 - hsvV];
    } else {
      colorComp = chroma(col.hex)[mode.func]();
      if (mode.func === "hcg") {
        colorComp = [colorComp[0], colorComp[1] / 100, colorComp[2] / 100];
      }
    }

    let pX = translate(
        colorComp[mode.x[0]],
        mode.x[2] || 0,
        mode.x[1],
        -cubeSize * 0.5,
        cubeSize * 0.5
      ),
      pY = translate(
        colorComp[mode.y[0]],
        mode.y[2] || 0,
        mode.y[1],
        -cubeSize * 0.5,
        cubeSize * 0.5
      ),
      pZ = translate(
        colorComp[mode.z[0]],
        mode.z[2] || 0,
        mode.z[1],
        -cubeSize * 0.5,
        cubeSize * 0.5
      );

    if (
      mode.func === "hsl" ||
      mode.func === "hsv" ||
      mode.func === "hsi" ||
      mode.func === "hcg"
    ) {
      let theta = (Math.PI * colorComp[mode.x[0]]) / 180;
      let r = colorComp[mode.y[0]] * cubeSize;

      if (mode.func === "hsi") {
        r *= colorComp[mode.z[0]] * 0.75;
      } else if (mode.func === "hsv") {
        r *= colorComp[mode.z[0]] * 0.5;
      } else if (mode.func === "hcg") {
        r *= 0.5;
      } else {
        r *=
          colorComp[mode.z[0]] < 0.5
            ? colorComp[mode.z[0]]
            : 1 - colorComp[mode.z[0]];
      }

      pY = r * Math.cos(theta);
      pX = r * Math.sin(theta);
    }

    if (mode.func === "lch") {
      let theta = (Math.PI * colorComp[mode.x[0]]) / 180;
      let r = translate(colorComp[mode.y[0]], 0, mode.y[1], 0, cubeSize * 0.5);

      pY = r * Math.cos(theta);
      pX = r * Math.sin(theta);
    }

    let particle = new THREE.Vector3(pX, pY, pZ),
      Tcolor = new THREE.Color(col.hex);

    colors.push(Tcolor);

    // add it to the geometry
    particles.vertices.push(particle);
  });

  // create the particle system
  const particleSystem = new THREE.Points(particles, pMaterial);

  particleSystem.name = "colors";
  particles.colors = colors;

  // add it to the scene
  objects.push(particleSystem);
  scene.add(particleSystem);
  part = particleSystem;
}

renderer.render(scene, cam);

animate();

function setSceneColor(color) {
  scene.background = new THREE.Color(color);
  scene.fog = new THREE.Fog(color, 150, 300); //new THREE.FogExp2(0x000000, 0.0007);
}
/*
var aspect = window.innerWidth / window.innerHeight;
var d = 20;
camera = new THREE.OrthographicCamera( - d * aspect, d * aspect, d, - d, 1, 1000 );

camera.position.set( 20, 20, 20 ); // all components equal
camera.lookAt( scene.position ); // or the origin
*/

function init() {
  cam = new THREE.PerspectiveCamera(75, width / height, 1, 500);
  cam.position.z = cubeSize * 1.5;
  scene = new THREE.Scene();
  setSceneColor(bg);
  root = new THREE.Object3D();

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(width, height);

  addCube();

  controls = new OrbitControls(cam, renderer.domElement);
  // controls.addEventListener( 'change', render ); // remove when using animation loop
  // enable animation loop when using damping or autorotation
  controls.enableDamping = true;
  controls.dampingFactor = 0.75;
  controls.enableZoom = true;
  controls.zoomSpeed = 0.25;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 2.0;
  controls.maxDistance = cubeSize * 1.75;
  controls.maxPolarAngle = Math.PI * 4;
  //controls.minPolarAngle = 0;
  controls.maxAzimuthAngle = Infinity;
  controls.minAzimuthAngle = -Infinity;

  controls.noPan = true;
  controls.noKeys = true;
  //controls.noZoom = true;

  const container = document.querySelector("#container");
  container.appendChild(renderer.domElement);

  window.addEventListener("resize", onWindowResize, false);

  document
    .querySelector("button")
    .addEventListener("click", toggleDarkMode, false);
}

function toggleDarkMode() {
  isDark = !isDark;

  document.querySelector("body").classList.toggle("isDark");
  var newColor = isDark ? cDark : cLight;

  setSceneColor(newColor);
  var colorspace = scene.getObjectByName("colorspace");
  scene.remove(colorspace);
  addCube(isDark ? "#ffffff" : cDark);

  document.documentElement.style.setProperty("--background", newColor);
  document.documentElement.style.setProperty(
    "--foreground",
    isDark ? "#ffffff" : cDark
  );
}

function addCube(color) {
  let geometryCube = cube(cubeSize);
  //geometryCube.computeLineDistances();
  const material = new THREE.LineBasicMaterial({
    color: color || 0xffffff,
    linewidth: 1,
    linecap: "round", //ignored by WebGLRenderer
    linejoin: "round", //ignored by WebGLRenderer
  });

  const colorspace = new THREE.LineSegments(geometryCube, material);

  colorspace.name = "colorspace";

  objects.push(colorspace);
  scene.add(colorspace);

  spaceCube = colorspace;
}

function cube(size) {
  const h = size * 0.5;
  const geometry = new THREE.Geometry();

  geometry.vertices.push(
    new THREE.Vector3(-h, -h, -h),
    new THREE.Vector3(-h, h, -h),
    new THREE.Vector3(-h, h, -h),
    new THREE.Vector3(h, h, -h),
    new THREE.Vector3(h, h, -h),
    new THREE.Vector3(h, -h, -h),
    new THREE.Vector3(h, -h, -h),
    new THREE.Vector3(-h, -h, -h),
    new THREE.Vector3(-h, -h, h),
    new THREE.Vector3(-h, h, h),
    new THREE.Vector3(-h, h, h),
    new THREE.Vector3(h, h, h),
    new THREE.Vector3(h, h, h),
    new THREE.Vector3(h, -h, h),
    new THREE.Vector3(h, -h, h),
    new THREE.Vector3(-h, -h, h),
    new THREE.Vector3(-h, -h, -h),
    new THREE.Vector3(-h, -h, h),
    new THREE.Vector3(-h, h, -h),
    new THREE.Vector3(-h, h, h),
    new THREE.Vector3(h, h, -h),
    new THREE.Vector3(h, h, h),
    new THREE.Vector3(h, -h, -h),
    new THREE.Vector3(h, -h, h)
  );

  return geometry;
}

function render() {
  const time = Date.now() * 0.0001;
  renderer.render(scene, cam);
  //controls.update();
  objects.forEach((object) => {
    //object.rotation.x = 0.25 * time * ( i%2 == 1 ? 1 : -1);
    object.rotation.x = 0.25 * time;
    object.rotation.y = 0.25 * time;
  });
}

function animate() {
  requestAnimationFrame(animate);
  render();
}

$select.addEventListener(
  "change",
  (e) => {
    colorMode = $select.value;
    objects = [];
    const colorspace = scene.getObjectByName("colorspace");
    scene.remove(colorspace);
    const colors = scene.getObjectByName("colors");
    scene.remove(colors);
    addParticles(colorList, colorMode);
    addCube(isDark ? cLight : cDark);
  },
  false
);

$selectList.addEventListener(
  "change",
  (e) => {
    const listName = $selectList.value;
    objects = [];
    const colorspace = scene.getObjectByName("colorspace");
    scene.remove(colorspace);
    const colors = scene.getObjectByName("colors");
    scene.remove(colors);
    fetchList(listName);
    //addParticles(colorList, colorMode);
    addCube(isDark ? cLight : cDark);
  },
  false
);

function translate(value, low1, high1, low2, high2) {
  return low2 + (high2 - low2) * ((value - low1) / (high1 - low1));
}

const PQ = function perceptual_quantizer(X) {
  const XX = Math.pow(X * 1e-4, 0.1593017578125);
  return Math.pow(
    (0.8359375 + 18.8515625 * XX) / (1 + 18.6875 * XX),
    134.034375
  );
};

function Jzazbz([X, Y, Z]) {
  const Lp = PQ(0.674207838 * X + 0.38279934 * Y - 0.047570458 * Z),
    Mp = PQ(0.14928416 * X + 0.73962834 * Y + 0.0833273 * Z),
    Sp = PQ(0.07094108 * X + 0.174768 * Y + 0.67097002 * Z),
    Iz = 0.5 * (Lp + Mp),
    az = 3.524 * Lp - 4.066708 * Mp + 0.542708 * Sp,
    bz = 0.199076 * Lp + 1.096799 * Mp - 1.295875 * Sp,
    Jz = (0.44 * Iz) / (1 - 0.56 * Iz) - 1.6295499532821566e-11;
  return [Jz, az, bz];
}

function cat022hpe(l, m, s) {
  var lh = 0.7409792 * l + 0.218025 * m + 0.0410058 * s,
    mh = 0.2853532 * l + 0.6242014 * m + 0.0904454 * s,
    sh = -0.009628 * l - 0.005698 * m + 1.015326 * s;

  return { lh: lh, mh: mh, sh: sh };
}

function xyz2cat02([x, y, z]) {
  const l = 0.7328 * x + 0.4296 * y - 0.1624 * z,
    m = -0.7036 * x + 1.6975 * y + 0.0061 * z,
    s = 0.003 * x + 0.0136 * y + 0.9834 * z;

  return [l, m, s];
}

function yuv(rgb) {
  return [
    /*Y*/ rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114,
    /*U*/ rgb[0] * -0.168736 + rgb[1] * -0.331264 + rgb[2] * 0.5 + 128,
    /*V*/ rgb[0] * 0.5 + rgb[1] * -0.418688 + rgb[2] * -0.081312 + 128,
  ];
}

function f(x) {
  if (x >= 0.0031308) return (1.055 * x) ^ (1.0 / 2.4 - 0.055);
  else return 12.92 * x;
}

function linear_srgb_to_oklab(rgb) {
  rgb = rgb.map((comp) => comp / 255);

  let l = 0.412165612 * rgb[0] + 0.536275208 * rgb[1] + 0.0514575653 * rgb[2];
  let m = 0.211859107 * rgb[0] + 0.6807189584 * rgb[1] + 0.107406579 * rgb[2];
  let s = 0.0883097947 * rgb[0] + 0.2818474174 * rgb[1] + 0.6302613616 * rgb[2];

  l = Math.cbrt(l);
  m = Math.cbrt(m);
  s = Math.cbrt(s);

  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

let rayCaster = new THREE.Raycaster();

document
  .querySelector("body")
  .addEventListener("mousemove", onDocumentMouseMove);

const $currentColor = document.querySelector(".currentColor");

let currentColorTimer;

function showColor(name, hex) {
  clearTimeout(currentColorTimer);
  $currentColor.innerHTML = `<div><h2>${name}</h2><span>${hex}</span></div>`;
  $currentColor.style = `--color: ${hex}`;
  currentColorTimer = setTimeout(() => {
    $currentColor.innerHTML = "";
  }, 3000);
}

// Highlight a color name using a raycaster at some point
function onDocumentMouseMove(event) {
  let mousePosition = {};
  event.preventDefault();
  mousePosition.x = (event.clientX / renderer.domElement.clientWidth) * 2 - 1;
  mousePosition.y = -(event.clientY / renderer.domElement.clientHeight) * 2 + 1;
  rayCaster.setFromCamera(mousePosition, cam);
  var intersects = rayCaster.intersectObjects(
    [scene.getObjectByName("colors")],
    false
  );
  if (intersects.length > 0) {
    var descriptions = [];
    for (var i = 0; i < intersects.length; i++) {
      //Only display those points we can SEE due to the near clipping plane.
      //Without this check, the ray caster will list them all, even if they're clipped by the near plane.
      //".distance" is relative to the camera, not absolute world units.
      if (intersects[i].distance > cam.near) {
        var description = "  " + colorList[intersects[i].index].name + ", ";
        description += "  " + colorList[intersects[i].index].hex + ", ";
        description +=
          "  Distance: " + intersects[i].distance.toFixed(2) + ", ";
        description +=
          "  Ray to point dist: " +
          intersects[i].distanceToRay.toFixed(2) +
          ", ";
        description += "  Index: " + intersects[i].index + ", ";
        description +=
          "  Coords: [" +
          intersects[i].point.x.toFixed(2) +
          ", " +
          intersects[i].point.y.toFixed(2) +
          ", " +
          intersects[i].point.z.toFixed(2) +
          "]";
        descriptions.push(description);

        showColor(
          colorList[intersects[i].index].name,
          colorList[intersects[i].index].hex
        );
        break;
      }

      if (descriptions.length > 0) {
        console.log(
          "Mouse pointer intersected the following points, closest to furthest: "
        );
        for (var v = 0; v < descriptions.length; v++)
          console.log(descriptions[v]);
      }
    }
  }
}
