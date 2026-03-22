import React from 'react';

export const OnboardingCard = ({ index, title, copy }) => {
  return (
    <div className="onboarding-card">
      <span className="onboarding-index">{index}</span>
      <strong>{title}</strong>
      <p>{copy}</p>
    </div>
  );
};
