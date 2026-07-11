const PLAT_COLORS = ['blue','purple','amber','emerald','rose','cyan','orange','teal','indigo','pink'];

const safelist = [];
for (const c of PLAT_COLORS) {
  safelist.push(`bg-${c}-100`, `bg-${c}-600`, `bg-${c}-600/20`, `text-${c}-400`, `text-${c}-600`);
}

module.exports = {
  content: ['../index.html'],
  safelist,
  theme: {
    extend: {},
  },
  plugins: [],
};
